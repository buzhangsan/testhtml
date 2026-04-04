#!/usr/bin/env bash
set -euo pipefail
export PATH=/root/clawd/tmp/gotmp/go/bin:$PATH
export TMPDIR=/root/clawd/tmp/gotmp/tmp
export GOTMPDIR=/root/clawd/tmp/gotmp/tmp
mkdir -p "$TMPDIR"
python3 /root/clawd/tmp/xiangseSkill/tools/scripts/xbs_tool.py json2xbs \
  -i /root/legado_xiangse_batch/out/xiangse_package_validated.json \
  -o /root/legado_xiangse_batch/out/xiangse_package_validated.xbs
python3 /root/clawd/tmp/xiangseSkill/tools/scripts/xbs_tool.py xbs2json \
  -i /root/legado_xiangse_batch/out/xiangse_package_validated.xbs \
  -o /root/legado_xiangse_batch/out/xiangse_package_validated.roundtrip.json
python3 /root/clawd/tmp/xiangseSkill/tools/scripts/check_xiangse_schema.py \
  /root/legado_xiangse_batch/out/xiangse_package_validated.roundtrip.json
