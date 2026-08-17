# Linux/Docker実行環境用のRunnerイメージ(M3-4 #94、docs/adr/0020-docker-runtime.md)。
# Claude Code CLI(Bedrock経由)+ このリポジトリのPythonパッケージを同梱し、
# 無人実行(Job Queue/Orchestratorから起動されるRunner)を想定する。
#
# シークレット(GitLab PAT・AWS/Bedrock認証情報)はこのイメージに一切含めない。
# 実行時に環境変数(docker run -e / docker-compose env_file)経由で渡すこと
# (docs/operations/docker-runtime.md参照)。
#
# ベースイメージ: ADR-0001の「Python 3.11以上」に合わせ、pipが標準で使えるDebian系の
# python:3.11-slim(bookworm)を使う(ADR-0020で選定)。
FROM python:3.11-slim

# git: Workspace Manager(bare clone + worktree、src/gitlab_ai_platform/workspace/)が
#      subprocessで呼び出す。
# curl/ca-certificates/gnupg: Node.js(NodeSourceリポジトリ)導入に使用。
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
        ca-certificates \
        gnupg \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLIはnpm経由で導入する(バージョンピン留め・社内npmミラーへの切り替えの
# しやすさを優先。ADR-0020「却下した選択肢」参照)。そのためNode.js 20.x(LTS)を追加する。
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code \
    && claude --version

# 無人実行での権限を最小化するため、非rootユーザーで実行する
RUN useradd --create-home --shell /bin/bash runner

WORKDIR /app

# 依存関係の定義だけ先にコピーしてDockerのレイヤーキャッシュを効かせる
# (src/を変更してもpyproject.toml未変更ならpip installのレイヤーは再利用される)
COPY pyproject.toml ./
COPY src ./src

# 開発用ツール(ruff/mypy/pytest)はイメージに含めない。実行に必要な依存(requests/mcp)のみ
RUN pip install --no-cache-dir .

# Workspace用ボリュームのマウントポイント(bare clone・worktree・実行ログ・
# レビュー結果・State/Job DBをまとめて永続化する。ADR-0020参照)
RUN mkdir -p /data/workspace /data/logs /data/reviews \
    && chown -R runner:runner /app /data

USER runner

# CLAUDE_CODE_USE_BEDROCKは認証方式の選択そのもの(シークレットではない)なのでここで既定化する。
# AWS_ACCESS_KEY_ID等の実際の認証情報は実行時に注入すること(イメージには含めない)
ENV CLAUDE_CODE_USE_BEDROCK=1

VOLUME ["/data"]

ENTRYPOINT ["gitlab-ai-platform"]
CMD ["--help"]
