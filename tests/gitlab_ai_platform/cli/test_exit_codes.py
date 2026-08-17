from __future__ import annotations

from gitlab_ai_platform.cli import exit_codes


def test_exit_codes_are_unique():
    values = [
        exit_codes.EXIT_OK,
        exit_codes.EXIT_UNEXPECTED_ERROR,
        exit_codes.EXIT_CONFIG_ERROR,
        exit_codes.EXIT_GITLAB_ADAPTER_ERROR,
        exit_codes.EXIT_WORKSPACE_ERROR,
        exit_codes.EXIT_RUNNER_ERROR,
        exit_codes.EXIT_REVIEW_ERROR,
        exit_codes.EXIT_STATE_STORE_ERROR,
        exit_codes.EXIT_ALREADY_RUNNING,
        exit_codes.EXIT_CLAUDE_NOT_FOUND,
        # M3-3(#93, docs/adr/0020-runner-process-separation.md): workerサブコマンド専用
        exit_codes.EXIT_JOB_ERROR,
        exit_codes.EXIT_INTERRUPTED,
    ]

    assert len(values) == len(set(values))


def test_exit_codes_do_not_collide_with_argparse_usage_error():
    # argparseは引数エラー時に終了コード2を使うため、パイプライン用のコードと重複させない
    assert 2 not in {
        exit_codes.EXIT_CONFIG_ERROR,
        exit_codes.EXIT_GITLAB_ADAPTER_ERROR,
        exit_codes.EXIT_WORKSPACE_ERROR,
        exit_codes.EXIT_RUNNER_ERROR,
        exit_codes.EXIT_REVIEW_ERROR,
        exit_codes.EXIT_STATE_STORE_ERROR,
    }
