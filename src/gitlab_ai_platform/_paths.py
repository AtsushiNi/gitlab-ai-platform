"""project名をファイルシステム上の安全な1階層ディレクトリ名に変換する共通ヘルパー。

`workspace`(worktreeディレクトリ)と`runner`(実行ログディレクトリ)の両方が、
project名(`group/subgroup/project`)から同じ規則でディレクトリ名を作る必要があり、
以前はそれぞれ独自に`_slugify_project`を実装していた(バイト単位で同一のコード)。
規則がずれるとRunnerのログパスとWorkspaceのworktreeパスの対応が取れなくなるため、
ここに集約する。

`gitlab_adapter._encode_project`(REST APIのURLパスセグメント用エンコード)は
たまたま同じ`quote(x, safe="")`を使っているが、目的が異なる(URLエンコード vs
ファイルシステム上のスラッグ化)別の関心事のため、あえて統合しない。
"""

from __future__ import annotations

from urllib.parse import quote, unquote


def slugify_project(project: str) -> str:
    # `group/subgroup/project`のようなスラッシュ区切りを、深いディレクトリ階層を作らずに
    # 1階層のディレクトリ名へ落とし込む(Spike S-3 §7、Windowsのパス長制限対策)。
    # 単純な"/"→"__"置換は、GitLabのproject/group名にアンダースコアが許可されているため
    # 単射でない(例: "ab/cd"と"ab__cd"が衝突する)。パーセントエンコーディングなら
    # 1階層のまま単射・可逆に変換できる
    return quote(project, safe="")


def deslugify_project(slug: str) -> str:
    return unquote(slug)


__all__ = ["deslugify_project", "slugify_project"]
