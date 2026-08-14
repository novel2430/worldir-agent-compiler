#!/bin/zsh
cd /Users/owen/Documents/hackday/worldir-agent-compiler || exit 1
secret_file="config/oneapi.env"

if [[ -s "$secret_file" ]]; then
  source "$secret_file"
  printf '已加载本机 OneAPI Key，正在测试两个模型...\n'
else
  printf '请输入 OneAPI Key（输入不会显示），然后按回车：'
  IFS= read -rs ONEAPI_API_KEY
  printf '\n正在保存本机密钥配置并测试两个模型...\n'
  umask 077
  printf 'export ONEAPI_API_KEY=%q\n' "$ONEAPI_API_KEY" > "$secret_file"
  chmod 600 "$secret_file"
  export ONEAPI_API_KEY
fi

uv run python scripts/test_oneapi_models.py
status=$?
unset ONEAPI_API_KEY

printf '\n测试结束，退出码：%s\n' "$status"
printf '密钥已保存在 %s，后续运行前执行：source %s\n' "$secret_file" "$secret_file"
printf '窗口将保持打开，方便查看结果。\n'
exec zsh
