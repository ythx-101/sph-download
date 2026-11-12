#!/usr/bin/env node
/**
 * 用手机号 + 短信验证码在无头 Chrome 里登录元宝网页端，把登录态存成本地文件（权限 600）。
 *
 * 用法：
 *   YB_PHONE=<你的手机号> node yb_sms_login.mjs          正式登录
 *   YB_PHONE=<你的手机号> node yb_sms_login.mjs --check  只检查环境：打开登录框、填好手机号就退出，不发短信
 *
 * 流程：脚本点「发送验证码」后创建 <状态目录>/NEED_CODE，然后等你把收到的验证码写进
 *       <状态目录>/sms.code（建议先 umask 077）。读到后自动提交，成功即写入登录态文件并删掉 sms.code。
 *
 * 环境变量：
 *   YB_PHONE         必填，接收验证码的手机号
 *   SPH_STATE_DIR    状态目录，默认 ~/.config/sph-download
 *   SPH_COOKIE_FILE  登录态文件，默认 <状态目录>/yuanbao.cookie
 *   CHROME_BIN       Chrome / Chromium 可执行文件，默认自动查找
 *   YB_CHROME_NO_SANDBOX=1  在容器里或以 root 运行时给 Chrome 加 --no-sandbox
 *   YB_CDP_PORT      调试端口，默认 9342
 *
 * 需要 Node.js 22 及以上（用到内置 WebSocket）。
 * 脚本不打印登录态的值，也不打印验证码；浏览器临时目录在系统临时目录下，退出时删除。
 */
import { spawn } from 'node:child_process';
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const CHECK_ONLY = process.argv.includes('--check');
const PHONE = (process.env.YB_PHONE || '').trim();
const expandHome = (p) => (p.startsWith('~') ? path.join(os.homedir(), p.slice(1)) : p);
const YB_DIR = path.resolve(expandHome(
  (process.env.SPH_STATE_DIR || '').trim() ||
  path.join((process.env.XDG_CONFIG_HOME || '').trim() || path.join(os.homedir(), '.config'), 'sph-download'),
));
const NEED_CODE_FILE = path.join(YB_DIR, 'NEED_CODE');
const SMS_CODE_FILE = path.join(YB_DIR, 'sms.code');
const COOKIE_OUT_FILE = path.resolve(expandHome((process.env.SPH_COOKIE_FILE || '').trim() || path.join(YB_DIR, 'yuanbao.cookie')));
const KEEP_COOKIES = ['hy_user', 'hy_token', 'hy_source'];
const CDP_PORT = Number(process.env.YB_CDP_PORT || 9342);
const PROFILE_DIR = path.join(os.tmpdir(), `yb_chr_login_${process.pid}`);
const MAX_WAIT_MS = 20 * 60 * 1000;
const MAX_RESENDS = 2;

const PHONE_SEL = '.hyc-login-v2 input[type="tel"], input[placeholder="请输入手机号"]';
const CODE_SEL = '.hyc-login-v2 input[type="number"], input[placeholder="请输入验证码"]';
const SEND_BTN_SEL = 'a.hyc-phone-login__send-code, .hyc-phone-login__send-code';
const LOGIN_BTN_SEL = 'button.hyc-phone-login__btn';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (msg) => console.log(`[yb-sms-login ${new Date().toISOString()}] ${msg}`);

function findChrome() {
  const custom = (process.env.CHROME_BIN || '').trim();
  if (custom) return custom;
  const fixed = [
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
  ];
  const names = ['chromium', 'chromium-browser', 'google-chrome', 'google-chrome-stable', 'chrome'];
  const dirs = (process.env.PATH || '').split(path.delimiter).filter(Boolean);
  const cands = [...fixed, ...dirs.flatMap((d) => names.map((n) => path.join(d, n)))];
  return cands.find((p) => { try { fs.accessSync(p, fs.constants.X_OK); return true; } catch { return false; } }) || null;
}

function getJson(url) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, { timeout: 5000 }, (res) => {
      let data = '';
      res.on('data', (c) => (data += c));
      res.on('end', () => {
        try { resolve(JSON.parse(data)); } catch (e) { reject(e); }
      });
    });
    req.on('error', reject);
    req.on('timeout', () => req.destroy(new Error('HTTP timeout')));
  });
}

class CDP {
  constructor(wsUrl) {
    this.ws = new WebSocket(wsUrl);
    this.id = 0;
    this.pending = new Map();
    this.onEvent = null;
  }
  async connect() {
    await new Promise((res, rej) => {
      this.ws.addEventListener('open', res);
      this.ws.addEventListener('error', rej);
    });
    this.ws.addEventListener('message', (ev) => {
      const msg = JSON.parse(String(ev.data));
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(JSON.stringify(msg.error)));
        else resolve(msg.result);
      } else if (msg.method && this.onEvent) {
        this.onEvent(msg.method, msg.params);
      }
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error(`CDP timeout on ${method}`));
        }
      }, 15000);
      this.pending.set(id, {
        resolve: (v) => { clearTimeout(timer); resolve(v); },
        reject: (e) => { clearTimeout(timer); reject(e); },
      });
    });
  }
  close() {
    try { this.ws.close(); } catch {}
  }
}

async function clickSelector(cdp, sel) {
  const res = await cdp.send('Runtime.evaluate', {
    returnByValue: true,
    expression: `(() => {
      const el = document.querySelector(${JSON.stringify(sel)});
      if (!el) return null;
      const r = el.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) return null;
      return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
    })()`,
  });
  const pt = res.result.value;
  if (!pt) throw new Error(`找不到可见元素: ${sel}`);
  await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: pt.x, y: pt.y, button: 'left', clickCount: 1 });
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: pt.x, y: pt.y, button: 'left', clickCount: 1 });
  await sleep(150);
}

async function fillInput(cdp, sel, text) {
  await clickSelector(cdp, sel);
  await cdp.send('Runtime.evaluate', {
    expression: `(() => {
      const el = document.querySelector(${JSON.stringify(sel)});
      if (el) { el.focus(); try { el.select && el.select(); } catch {} }
    })()`,
  });
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'End', code: 'End', windowsVirtualKeyCode: 35 });
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'End', code: 'End', windowsVirtualKeyCode: 35 });
  for (let i = 0; i < 16; i++) {
    await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Backspace', code: 'Backspace', windowsVirtualKeyCode: 8 });
    await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Backspace', code: 'Backspace', windowsVirtualKeyCode: 8 });
  }
  await cdp.send('Input.insertText', { text });
  await sleep(200);
}

(async () => {
  if (!/^\d{6,15}$/.test(PHONE)) {
    throw new Error('请用环境变量 YB_PHONE 提供接收验证码的手机号（纯数字）');
  }
  if (typeof WebSocket === 'undefined') {
    throw new Error('需要 Node.js 22 及以上（内置 WebSocket）');
  }
  const chromeBin = findChrome();
  if (!chromeBin) {
    throw new Error('找不到 Chrome / Chromium，请安装或用环境变量 CHROME_BIN 指定');
  }
  fs.mkdirSync(YB_DIR, { recursive: true, mode: 0o700 });
  fs.mkdirSync(path.dirname(COOKIE_OUT_FILE), { recursive: true, mode: 0o700 });
  fs.mkdirSync(PROFILE_DIR, { recursive: true, mode: 0o700 });
  const chrome = spawn(chromeBin, [
    '--headless=new',
    ...(process.env.YB_CHROME_NO_SANDBOX === '1' ? ['--no-sandbox'] : []),
    '--disable-gpu',
    '--disable-dev-shm-usage',
    '--window-size=1280,900',
    '--lang=zh-CN',
    `--remote-debugging-port=${CDP_PORT}`,
    `--user-data-dir=${PROFILE_DIR}`,
    'about:blank',
  ], { stdio: 'ignore', detached: true });

  const cleanup = () => {
    // 连同子进程一起结束，等它们退干净再删临时目录
    try { process.kill(-chrome.pid, 'SIGKILL'); } catch { try { chrome.kill('SIGKILL'); } catch {} }
    for (let i = 0; i < 3; i++) {
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 300);
      try { fs.rmSync(PROFILE_DIR, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); } catch {}
    }
    try { fs.unlinkSync(NEED_CODE_FILE); } catch {}
  };
  process.on('SIGINT', () => { cleanup(); process.exit(130); });
  process.on('SIGTERM', () => { cleanup(); process.exit(143); });

  let cdp = null;
  try {
    let page = null;
    for (let i = 0; i < 30; i++) {
      await sleep(300);
      try {
        const list = await getJson(`http://127.0.0.1:${CDP_PORT}/json/list`);
        page = list.find((t) => t.type === 'page');
        if (page) break;
      } catch {}
    }
    if (!page) throw new Error('连不上无头 Chrome（在容器里或以 root 运行时请加 YB_CHROME_NO_SANDBOX=1）');

    cdp = new CDP(page.webSocketDebuggerUrl);
    await cdp.connect();
    const netEvents = [];
    cdp.onEvent = (method, params) => {
      if (method === 'Network.responseReceived') {
        const u = params.response.url || '';
        if (u.includes('/api/login/') || u.includes('/api/joint/')) {
          netEvents.push({ ts: Date.now(), requestId: params.requestId, status: params.response.status, url: u.split('?')[0] });
        }
      }
    };

    await cdp.send('Page.enable');
    await cdp.send('Network.enable');
    await cdp.send('Network.setExtraHTTPHeaders', { headers: { 'Accept-Language': 'zh-CN,zh;q=0.9' } });
    await cdp.send('Page.navigate', { url: 'https://yuanbao.tencent.com/' });

    // 登录框有时要十几秒才弹出来（首次启动浏览器更慢），最多等 30 秒
    for (let i = 0; i < 75; i++) {
      await sleep(400);
      const ok = await cdp.send('Runtime.evaluate', {
        returnByValue: true,
        expression: `(() => {
          if (document.querySelector('.hyc-login-v2')) return true;
          const btn = Array.from(document.querySelectorAll('button, div, span, a'))
            .find(b => b.innerText && /^(登录|Log In)$/i.test(b.innerText.trim()));
          if (btn) btn.click();
          return !!document.querySelector('.hyc-login-v2');
        })()`,
      });
      if (ok.result.value) break;
    }

    let phoneTabReady = false;
    for (let i = 0; i < 40; i++) {
      const ok = await cdp.send('Runtime.evaluate', {
        returnByValue: true,
        expression: `(() => {
          const rInput = document.querySelector('.hyc-login-v2 input[type="radio"][value="phone"]');
          const label = rInput?.closest('label') ||
            Array.from(document.querySelectorAll('.hyc-login-v2 label, .t-radio-button'))
              .find(l => /手机|Phone/i.test(l.innerText || ''));
          if (label) label.click();
          return !!document.querySelector(${JSON.stringify(PHONE_SEL)});
        })()`,
      });
      if (ok.result.value) { phoneTabReady = true; break; }
      await sleep(400);
    }
    if (!phoneTabReady) {
      throw new Error('没有等到手机号登录框（网络慢或页面结构变了）。请重试，或改用 README 里的手动登录方式');
    }

    const ensureReady = async () => {
      const st = await cdp.send('Runtime.evaluate', {
        returnByValue: true,
        expression: `(() => ({
          checked: !!document.querySelector('.hyc-login__agreement input[type="checkbox"]')?.checked,
          phoneVal: document.querySelector(${JSON.stringify(PHONE_SEL)})?.value || ''
        }))()`,
      });
      if (!st.result.value.checked) {
        await clickSelector(cdp, '.hyc-login__agreement .t-checkbox__input, .hyc-login__agreement label.t-checkbox');
      }
      if (st.result.value.phoneVal !== PHONE) {
        await fillInput(cdp, PHONE_SEL, PHONE);
      }
    };

    const sendSms = async (attempt) => {
      await ensureReady();
      for (let w = 0; w < 65; w++) {
        const txtRes = await cdp.send('Runtime.evaluate', {
          returnByValue: true,
          expression: `document.querySelector(${JSON.stringify(SEND_BTN_SEL)})?.innerText?.trim() || ''`,
        });
        if (txtRes.result.value && !/\d+\s*s/i.test(txtRes.result.value)) break;
        await sleep(1000);
      }
      const clickTs = Date.now();
      log(`点击发送验证码 (第 ${attempt} 次)...`);
      await clickSelector(cdp, SEND_BTN_SEL);
      let smsEv = null;
      for (let i = 0; i < 14; i++) {
        await sleep(500);
        smsEv = netEvents.find((e) => e.ts >= clickTs && e.url.includes('/api/login/getverificationcode'));
        if (smsEv) break;
      }
      if (!smsEv || smsEv.status !== 200) {
        throw new Error('验证码没有发出去（页面可能要求人机验证，或发送太频繁）。请稍后再试，或改用 README 里的手动登录方式');
      }
      const now = new Date();
      fs.writeFileSync(NEED_CODE_FILE, '');
      fs.utimesSync(NEED_CODE_FILE, now, now);
      const mtime = fs.statSync(NEED_CODE_FILE).mtimeMs;
      log(`短信已发送，已创建 ${NEED_CODE_FILE}，等待 ${SMS_CODE_FILE} 写入（最多 20 分钟）...`);
      return mtime;
    };

    if (CHECK_ONLY) {
      await ensureReady();
      const ready = await cdp.send('Runtime.evaluate', {
        returnByValue: true,
        expression: `(() => ({
          phone: (document.querySelector(${JSON.stringify(PHONE_SEL)})?.value || '').length,
          code: !!document.querySelector(${JSON.stringify(CODE_SEL)}),
          send: !!document.querySelector(${JSON.stringify(SEND_BTN_SEL)}),
        }))()`,
      });
      const r = ready.result.value;
      if (r.phone !== PHONE.length || !r.code || !r.send) {
        throw new Error('登录框没有准备好（页面结构可能变了）。请改用 README 里的手动登录方式');
      }
      log(`环境检查通过：登录框已就绪，手机号已填入。没有发送短信。状态目录 ${YB_DIR}`);
      cdp.close();
      cleanup();
      process.exit(0);
    }

    let resends = 0;
    let needMtime = await sendSms(1);
    let lastConsumed = 0;
    const deadline = Date.now() + MAX_WAIT_MS;

    while (Date.now() < deadline) {
      await sleep(1000);
      if (!fs.existsSync(SMS_CODE_FILE)) continue;
      let st;
      try { st = fs.statSync(SMS_CODE_FILE); } catch { continue; }
      if (st.mtimeMs < needMtime - 500 || st.mtimeMs <= lastConsumed) continue;
      await sleep(300);
      const m = fs.readFileSync(SMS_CODE_FILE, 'utf8').trim().match(/\b(\d{4,8})\b/);
      if (!m) continue;
      lastConsumed = fs.statSync(SMS_CODE_FILE).mtimeMs;
      try { fs.unlinkSync(SMS_CODE_FILE); } catch {}

      log('读到验证码，正在提交登录...');
      await ensureReady();
      await fillInput(cdp, CODE_SEL, m[1]);
      await clickSelector(cdp, LOGIN_BTN_SEL);

      let ok = false;
      for (let i = 0; i < 30; i++) {
        await sleep(500);
        const { cookies } = await cdp.send('Network.getAllCookies');
        if (cookies.some((c) => c.name === 'hy_token' && c.value)) {
          ok = true;
          break;
        }
      }
      if (ok) {
        await sleep(3000);
        const { cookies } = await cdp.send('Network.getAllCookies');
        // 只保存解析需要的三项，其余浏览器 Cookie 一律不落盘
        const cmap = new Map();
        for (const name of KEEP_COOKIES) {
          const same = cookies.filter((c) => c.name === name && c.value);
          const hit = same.find((c) => (c.domain || '').includes('yuanbao')) || same[0];
          if (hit) cmap.set(name, hit.value);
        }
        if (!cmap.has('hy_source')) cmap.set('hy_source', 'web');
        if (!cmap.has('hy_user') || !cmap.has('hy_token')) {
          throw new Error('登录后没有取到完整的登录态，请重试或改用 README 里的手动登录方式');
        }
        const header = Array.from(cmap.entries()).map(([k, v]) => `${k}=${v}`).join('; ');
        fs.writeFileSync(COOKIE_OUT_FILE, header + '\n', { mode: 0o600 });
        fs.chmodSync(COOKIE_OUT_FILE, 0o600);
        log(`登录成功！登录态已写入 ${COOKIE_OUT_FILE}（${cmap.size} 项，权限 600）。`);
        cdp.close();
        cleanup();
        process.exit(0);
      }

      if (resends < MAX_RESENDS) {
        resends++;
        log(`验证码可能已过期或无效，执行第 ${resends}/${MAX_RESENDS} 次重发...`);
        needMtime = await sendSms(resends + 1);
      } else {
        throw new Error('验证码校验未通过且已达最大重发次数');
      }
    }
    throw new Error('等了 20 分钟也没有收到验证码文件，已退出');
  } finally {
    if (cdp) cdp.close();
    cleanup();
  }
})().catch((e) => {
  log(`错误: ${e.message || e}`);
  process.exit(1);
});
