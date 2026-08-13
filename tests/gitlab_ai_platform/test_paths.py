from __future__ import annotations

import pytest

from gitlab_ai_platform._paths import deslugify_project, slugify_project


def test_slugify_project_is_injective_for_projects_with_literal_underscore():
    # "/"を"__"に置換する単純な方式は、GitLabのproject/group名にアンダースコアが
    # 許可されているため単射でなかった(別プロジェクトのbare clone/worktree/ログ
    # ディレクトリを共有してしまう)。workspace/runner両方が使う共通の回帰テスト
    assert slugify_project("ab/cd") != slugify_project("ab__cd")


@pytest.mark.parametrize(
    "project",
    ["group/project", "group/sub/project", "a_/b", "a__b", "ab__cd", "trailing_/slash"],
)
def test_deslugify_project_is_inverse_of_slugify(project: str):
    assert deslugify_project(slugify_project(project)) == project
