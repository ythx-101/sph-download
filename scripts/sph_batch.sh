#!/usr/bin/env bash
# sph_batch.sh：串行批量保存视频号分享链接。
#
# 用法：
#   sph_batch.sh <链接清单.txt> [--out 目录] [-- 透传给 sph 的参数...]
#
# 链接清单每行一个链接（或短码）；空行和 # 开头的行会跳过，重复的只跑一次。
# 规则：
#   - 严格串行，每条之间随机等 5～10 秒；任意 60 秒内最多开始 6 条。
#   - 输出目录里已经有 video.mp4 的短码会跳过，所以中断后重跑同一条命令即可续上。
#   - sph 退出码 3（登录态失效）时立刻停下，不再往下试；重新登录后重跑。
#   - 结果逐条追加到 <输出目录>/batch.tsv（时间、短码、退出码、耗时秒、链接）。
#
# 环境变量：
#   SPH_BIN            sph 命令的路径（默认：本脚本同目录下的 sph）
#   SPH_BATCH_MIN_GAP  每条之间最短等待秒数（默认 5）
#   SPH_BATCH_MAX_GAP  每条之间最长等待秒数（默认 10）
#   SPH_BATCH_PER_MIN  每 60 秒最多开始几条（默认 6）
#   SPH_BATCH_MIN_FREE_MB  输出目录所在磁盘的最低可用空间，低于它就停（默认 300）
#
# 退出码：0 全部成功或已跳过；1 有失败的；3 登录态失效而中止；5 磁盘空间不足而中止；2 用法错误。

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPH_BIN="${SPH_BIN:-$HERE/sph}"
MIN_GAP="${SPH_BATCH_MIN_GAP:-5}"
MAX_GAP="${SPH_BATCH_MAX_GAP:-10}"
PER_MIN="${SPH_BATCH_PER_MIN:-6}"
MIN_FREE_MB="${SPH_BATCH_MIN_FREE_MB:-300}"

usage() { sed -n '2,6p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2; exit 2; }

LIST=""
OUT="out"
EXTRA=()
while [ $# -gt 0 ]; do
  case "$1" in
    --out) [ $# -ge 2 ] || usage; OUT="$2"; shift 2 ;;
    --) shift; EXTRA=("$@"); break ;;
    -h|--help) usage ;;
    -*) echo "[batch] 不认识的参数: $1" >&2; usage ;;
    *) [ -z "$LIST" ] || usage; LIST="$1"; shift ;;
  esac
done
[ -n "$LIST" ] && [ -f "$LIST" ] || { echo "[batch] 找不到链接清单: ${LIST:-（未给出）}" >&2; usage; }
[ -x "$SPH_BIN" ] || { echo "[batch] 找不到可执行的 sph: $SPH_BIN" >&2; exit 2; }
if [ "$MAX_GAP" -lt "$MIN_GAP" ] || [ "$PER_MIN" -lt 1 ]; then
  echo "[batch] 间隔或限速设置不对" >&2; exit 2
fi

mkdir -p "$OUT" || exit 2
TSV="$OUT/batch.tsv"
[ -f "$TSV" ] || printf 'time\tid\trc\tseconds\tlink\n' > "$TSV"

say() { echo "[batch $(date '+%H:%M:%S')] $*"; }

short_id() {  # 从一行里取短码；取不出来就输出空
  local s="$1"
  if [[ "$s" =~ weixin\.qq\.com/sph/([A-Za-z0-9_-]+) ]]; then echo "${BASH_REMATCH[1]}"
  elif [[ "$s" =~ [?\&]id=([A-Za-z0-9_-]+) ]]; then echo "${BASH_REMATCH[1]}"
  elif [[ "$s" =~ ^[A-Za-z0-9_-]{6,32}$ ]]; then echo "$s"
  fi
}

# 读清单：去空行、注释、首尾空白、重复
LINKS=()
declare -A SEEN=()
while IFS= read -r line || [ -n "$line" ]; do
  line="${line%$'\r'}"
  line="${line#"${line%%[![:space:]]*}"}"; line="${line%"${line##*[![:space:]]}"}"
  [ -z "$line" ] && continue
  [ "${line:0:1}" = "#" ] && continue
  key="$(short_id "$line")"; key="${key:-$line}"
  [ -n "${SEEN[$key]:-}" ] && continue
  SEEN[$key]=1
  LINKS+=("$line")
done < "$LIST"

TOTAL=${#LINKS[@]}
[ "$TOTAL" -gt 0 ] || { say "清单里没有链接"; exit 0; }
say "共 $TOTAL 条，输出到 $OUT，每条间隔 ${MIN_GAP}～${MAX_GAP} 秒，每分钟最多 $PER_MIN 条"

STARTS=()   # 最近几次开始时间（秒），用来做 60 秒滑动窗口限速
OK=0; SKIP=0; FAIL=0; RAN=0; FINAL_RC=0
FAILED_LINKS=()

for i in "${!LINKS[@]}"; do
  link="${LINKS[$i]}"
  n=$((i + 1))
  id="$(short_id "$link")"

  if [ -n "$id" ] && [ -s "$OUT/$id/video.mp4" ]; then
    say "[$n/$TOTAL] 跳过 $id（已有 video.mp4）"
    SKIP=$((SKIP + 1))
    continue
  fi

  free_mb=$(df -Pm "$OUT" | awk 'NR==2 {print $4}')
  if [ -n "$free_mb" ] && [ "$free_mb" -lt "$MIN_FREE_MB" ]; then
    say "磁盘可用空间只剩 ${free_mb}MB（下限 ${MIN_FREE_MB}MB），停止。剩余 $((TOTAL - i)) 条未跑。"
    FINAL_RC=5
    break
  fi

  if [ "$RAN" -gt 0 ]; then
    gap=$((MIN_GAP + RANDOM % (MAX_GAP - MIN_GAP + 1)))
    say "等待 ${gap} 秒"
    sleep "$gap"
  fi
  # 滑动窗口：往前数第 PER_MIN 次开始如果还在 60 秒内，就等到它满 60 秒
  if [ "${#STARTS[@]}" -ge "$PER_MIN" ]; then
    oldest="${STARTS[$(( ${#STARTS[@]} - PER_MIN ))]}"
    wait_s=$(( oldest + 60 - $(date +%s) ))
    if [ "$wait_s" -gt 0 ]; then
      say "限速：再等 ${wait_s} 秒（每分钟最多 $PER_MIN 条）"
      sleep "$wait_s"
    fi
  fi

  t0=$(date +%s)
  STARTS+=("$t0")
  RAN=$((RAN + 1))
  say "[$n/$TOTAL] 开始 ${id:-$link}"
  "$SPH_BIN" "$link" --out "$OUT" ${EXTRA[@]+"${EXTRA[@]}"}
  rc=$?
  secs=$(( $(date +%s) - t0 ))
  printf '%s\t%s\t%s\t%s\t%s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "${id:--}" "$rc" "$secs" "$link" >> "$TSV"

  if [ "$rc" -eq 0 ]; then
    OK=$((OK + 1))
    say "[$n/$TOTAL] 完成 ${id:-$link}（${secs} 秒）"
  elif [ "$rc" -eq 3 ]; then
    FAIL=$((FAIL + 1)); FAILED_LINKS+=("$link")
    say "[$n/$TOTAL] 登录态失效（退出码 3），停止。重新登录元宝后重跑本命令即可续上。剩余 $((TOTAL - n)) 条未跑。"
    FINAL_RC=3
    break
  else
    FAIL=$((FAIL + 1)); FAILED_LINKS+=("$link")
    say "[$n/$TOTAL] 失败 ${id:-$link}（退出码 $rc），继续下一条"
  fi
done

say "结束：成功 $OK，跳过 $SKIP，失败 $FAIL，共 $TOTAL 条。记录在 $TSV"
if [ "${#FAILED_LINKS[@]}" -gt 0 ]; then
  say "失败的链接："
  printf '  %s\n' "${FAILED_LINKS[@]}"
fi
[ "$FINAL_RC" -ne 0 ] && exit "$FINAL_RC"
[ "$FAIL" -gt 0 ] && exit 1
exit 0
