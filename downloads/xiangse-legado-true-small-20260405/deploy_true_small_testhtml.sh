#!/usr/bin/env bash
set -euo pipefail
REPO_DIR=/root/testhtml
SRC_DIR=/root/legado_xiangse_batch/out
BATCH_DIR=downloads/xiangse-legado-true-small-20260405
BRANCH=main

if [ -d "$REPO_DIR/.git" ]; then
  cd "$REPO_DIR"
  git fetch origin "$BRANCH"
  git checkout "$BRANCH"
  git pull --ff-only origin "$BRANCH"
else
  git clone https://github.com/buzhangsan/testhtml.git "$REPO_DIR"
  cd "$REPO_DIR"
  git checkout "$BRANCH" || git checkout -b "$BRANCH"
fi

mkdir -p "$BATCH_DIR"
cp "$SRC_DIR/xiangse_true_small.json" "$BATCH_DIR/xiangse-true-small.json"
cp "$SRC_DIR/xiangse_true_small.xbs" "$BATCH_DIR/xiangse-true-small.xbs"
cp "$SRC_DIR/xiangse_true_small.report.json" "$BATCH_DIR/xiangse-true-small.report.json"
cp "$SRC_DIR/xiangse_true_small.summary.txt" "$BATCH_DIR/xiangse-true-small.summary.txt"
cp "$SRC_DIR/xiangse_true_small.roundtrip.json" "$BATCH_DIR/xiangse-true-small.roundtrip.json"
cp "$SRC_DIR/快眼看书-true.sim.json" "$BATCH_DIR/kaiyan.sim.json"
cp "$SRC_DIR/顶点小说-true.sim.json" "$BATCH_DIR/dingdian.sim.json"
cp "$SRC_DIR/空白小说-true.sim.json" "$BATCH_DIR/kongbai.sim.json"
cp "$SRC_DIR/何以生肖-true.sim.json" "$BATCH_DIR/heyishengxiao.sim.json"
cp "$SRC_DIR/殓师灵异-true.sim.json" "$BATCH_DIR/lianshilingyi.sim.json"
cp /root/legado_xiangse_batch/build_true_small_pack.py "$BATCH_DIR/build_true_small_pack.py"
cp /root/legado_xiangse_batch/deploy_true_small_testhtml.sh "$BATCH_DIR/deploy_true_small_testhtml.sh"

cat > "$BATCH_DIR/index.html" <<'EOF'
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>Xiangse Legado True Small Pack (2026-04-05)</title>
</head>
<body>
  <h1>Xiangse Legado True Small Pack (2026-04-05)</h1>
  <p>只保留 5 个 GET + HTML + 无登录 + 无复杂 Java/JS 依赖的源，并按香色原生规则重写后实测通过 搜索 → 详情 → 目录 → 正文。</p>
  <ul>
    <li><a href="./xiangse-true-small.json">香色 JSON 包</a></li>
    <li><a href="./xiangse-true-small.xbs">香色 XBS 包</a></li>
    <li><a href="./xiangse-true-small.report.json">总报告 JSON</a></li>
    <li><a href="./xiangse-true-small.summary.txt">摘要 TXT</a></li>
    <li><a href="./xiangse-true-small.roundtrip.json">XBS 回转 JSON</a></li>
    <li><a href="./kaiyan.sim.json">快眼看书模拟报告</a></li>
    <li><a href="./dingdian.sim.json">顶点小说模拟报告</a></li>
    <li><a href="./kongbai.sim.json">空白小说模拟报告</a></li>
    <li><a href="./heyishengxiao.sim.json">何以生肖模拟报告</a></li>
    <li><a href="./lianshilingyi.sim.json">殓师灵异模拟报告</a></li>
    <li><a href="./build_true_small_pack.py">构建脚本</a></li>
    <li><a href="./deploy_true_small_testhtml.sh">部署脚本</a></li>
  </ul>
</body>
</html>
EOF

git add "$BATCH_DIR"
if git diff --cached --quiet; then
  echo "NO_CHANGES"
else
  git commit -m "Add Xiangse true small pack (2026-04-05)"
  git push origin "$BRANCH"
fi

git rev-parse HEAD
