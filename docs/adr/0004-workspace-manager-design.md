# ADR-0004: Workspace Manager の設計

- Issue: [#34](https://github.com/AtsushiNi/gitlab-ai-platform/issues/34) (M1-6)
- 状態: 決定

## 背景・制約

- `docs/architecture.md` の設計方針により、Workspace Managerは「プロジェクトごとのbare clone、
  MR単位のworktree作成/更新/破棄、ディスク上限とGCを管理する」ことが責務。「並列レビューで
  working treeを共有しない」ことが前提で、「git操作以外(ビルド・テスト実行など)はしない」ことが
  境界。
- `references/spike-S3-git-worktree-windows.md`(S-3)で以下を検証済み:
  - bare clone + MR単位worktreeの構成自体は問題なく動作し、objectはbare repoと共有される。
  - bare repoへのfetchを`refs/heads/*:refs/heads/*`のように直接上書きする方式は、対象branchが
    別worktreeでcheckout中だと拒否される。`refs/heads/*:refs/remotes/origin/*`のリモート
    トラッキング方式なら競合しない。
  - worktreeディレクトリを直接消してもGit管理上は壊れず、`git worktree prune`で復旧できる。
  - Windowsのパス長制限(MAX_PATH)を避けるため、worktreeのディレクトリ名は`mr-<iid>`のような
    短い識別子に留め、プロジェクト名やbranch名をそのままパスに含めない方がよい。

## 決定

### インターフェースは`typing.Protocol`を使う(ADR-0002/0003と同じ方針)

`WorkspaceManager`(`src/gitlab_ai_platform/workspace/protocol.py`)は`prepare` / `discard` /
`collect_garbage`の3メソッドのみを持つ。呼び出し側(MR Poller/Claude Code Runner/CLI)はこの
Protocol型だけを見て実装し、git実装(`GitWorkspaceManager`)に直接依存しない。将来Linux/Docker側
(M3-4)で実装を差し替える際も、呼び出し側の変更は不要になる想定(`docs/architecture.md`の
「MVP → AI Platformへの成長パス」表)。

### bare cloneとworktreeの2階層を、呼び出し側からは「MR単位のworktree」1つの操作に見せる

ディレクトリ構成:

```
<root>/repos/<slug>.git             # プロジェクト単位のbare clone
<root>/worktrees/<slug>/mr-<iid>/   # MR単位のworktree
```

`slug`はproject名(`group/subgroup/project`)をパーセントエンコーディング(`urllib.parse.quote`、
`gitlab_adapter._encode_project`と同じ方式)したもの。単純な`/`→`__`置換は、GitLabの
project/group名にアンダースコアが許可されているため単射でなく(例: `ab/cd`と`ab__cd`が
衝突し、別プロジェクトのbare cloneを共有してしまう)、パーセントエンコーディングに変更した。
S-3 §7の「深いディレクトリ階層を避ける」という助言に従い、GitLabのグループ/サブグループ階層を
そのままディレクトリ階層化しない(1階層のディレクトリ名に潰す)点は変わらない。worktree側の
ディレクトリ名も`mr-<iid>`という短い識別子のみとし、branch名やプロジェクト名を含めない
(同じくS-3 §7)。

呼び出し側が意識するのは`prepare(project, mr_iid, ref) -> WorktreeHandle`(用意)と
`discard(project, mr_iid)`(破棄)の2操作のみで、bare cloneの存在確認・作成・fetchは
`prepare`の内部で自動的に行う。

### `prepare`の`ref`引数はbranch名・commit shaのどちらも受け付ける

State Store(M1-4)は`commit_sha`単位でレビュー状態を追跡するため、Workspace Managerも
「branchの最新」ではなく「指定commitそのもの」を再現できる必要がある。`ref`をbranch名/commit sha
どちらでも受け付ける形にし、呼び出し側(Poller)が検出した`commit_sha`をそのまま渡せるようにした。

### bare repoへのfetchは`refs/heads/*:refs/remotes/origin/*`のみを使う

S-3 §4.2の検証結果通り、`refs/heads/*:refs/heads/*`のような直接上書き方式は稼働中のworktreeが
対象branchをcheckoutしていると失敗する。Workspace Managerは常に
`git fetch origin '+refs/heads/*:refs/remotes/origin/*'`のみを使い、各worktree側は自分自身の中で
`git reset --hard <ref>`することで最新化する(bare repoの`refs/heads/*`自体は更新しない)。

### branch名解決は`refs/remotes/origin/<ref>`を優先する

実装時に発覚した落とし穴: `git clone --bare`は初回clone時点の全branchを`refs/heads/*`に
**直接**コピーする(通常の`git clone`が`refs/remotes/origin/*`に置くのと異なる)。上記の方針で
以降`refs/heads/*`はfetchで更新されないため、そのまま`ref`(branch名)をworktreeの
`reset --hard`に渡すと、初回clone時点のstaleなcommitを指し続けてしまう
(`git_workspace.py`の`_resolve_ref`のdocstring/コメントに詳細を記載)。

対策として、`ref`の解決時に`refs/remotes/origin/<ref>`の存在を優先的に調べ、存在すればそちらを
使う。存在しなければ(=commit shaが渡された場合)`ref`をそのまま使う。commit shaは常にfetch済みの
履歴内であればobject自体は既にローカルに存在するため、ref namespaceに関わらず解決できる。

### ディスク上限とGCは`<root>/worktrees/`配下の合計サイズのみで判定する

bare repo(`<root>/repos/`)はobjectの共有ストアであり、worktree間で重複しない。GCの対象は
worktree(作業コピー)側のみとし、bare repoは対象外とする。

`prepare`は新規worktree作成の直前に現在のディスク使用量を確認し、上限を超えていれば
`collect_garbage`を内部で呼び出す。`collect_garbage`は各worktreeの最終利用時刻
(`prepare`実行時に`os.utime`で更新するマーカー)を基準に、最も古いものから上限を満たすまで
順に破棄する(LRU)。破棄しても上限を満たせない場合(GC対象が1つも無い等)は
`DiskLimitExceededError`を送出する。

### worktree破棄(`discard`)は冪等にする

対象worktreeが存在しない場合も例外を送出せず、何もしない(`rm -f`と同様)。State Storeの
`RecordNotFoundError`(存在しないレコードの更新はバグとして扱う)とは異なり、worktreeは
「クリーンアップ操作の呼び出し忘れ・二重呼び出し」が起きても呼び出し側に負担をかけたくない
ため、意図的に非対称な設計とした。

### 認証(PAT/SSH)の詳細はこのモジュールの責務外とする

S-3 §8で検証した「credential helper経由でPATを環境変数から都度供給する」「`core.sshCommand`で
Bot専用鍵をrepo単位に強制する」という認証方式そのものは、Workspace Managerが決め打ちしない。
コンストラクタの`git_config`(全gitコマンド呼び出しに`-c key=value`として注入される)を通じて
呼び出し側(config層/CLIの組み立てコード)が注入する形にした。Workspace Manager自身は
「GitLabの認証方式が何か」を知らない。

### `clone_url_for`もこのモジュールの責務外とする

project名からbare clone用URLを組み立てるロジック(GitLabのbase URL構築等)もコンストラクタの
`Callable[[str], str]`として注入する。GitLab固有のURL構築ロジックをWorkspace Manager内に
持たせず、テスト時はローカルの一時リポジトリパスを直接返す関数を注入できるようにした
(実サービスに繋がずテストする、というCLAUDE.mdのテスト方針に合わせた設計)。

## 却下した選択肢

- **`abc.ABC`による抽象基底クラス**: ADR-0002/0003と同じ理由で見送り。
- **worktreeディレクトリ名にbranch名やプロジェクトのフルパスを含める**: 可読性は上がるが、
  S-3 §7で確認したWindowsのパス長制限(MAX_PATH=260文字)に抵触しやすくなる。`mr-<iid>`という
  短い識別子で十分(branch名はworktree内で`git log`/`git branch`から参照できる)。
- **fetch戦略として`refs/heads/*:refs/heads/*`の直接上書き**: S-3 §4.2で「稼働中worktreeが
  対象branchをcheckout中だと拒否される」ことを確認済みのため不採用。
- **ディスク上限判定にbare repoも含める**: bare repoはproject単位で1つしか持たず、複数worktree間で
  objectが共有されるため増分は小さい。判定対象に含めても実害は小さいが、「何を破棄すれば
  空くか」という運用上の意味が明確な`worktrees/`配下のみを対象にした。
- **`discard`が対象不在時に例外を送出する(State Storeと同じ設計)**: worktreeの破棄はRunner実行後の
  後片付けであり、呼び出し側の実行順序次第で「既に無い」状態が正常系として起こりうる
  (異常終了からの再実行等)。エラーにする実利が薄いため冪等にした。
- **PAT/SSH認証をWorkspace Manager内で組み立てる**: GitLab Adapter(M1-1〜M1-3)が担う認証の
  責務と重複し、モジュール境界(`docs/architecture.md`)が曖昧になる。呼び出し側からの注入に留めた。

## 影響

- MR Poller(M1-5)は`WorkspaceManager`(Protocol型)にのみ依存し、`prepare`でworktreeを用意して
  Claude Code Runner(M1-7)に`WorktreeHandle.path`を渡し、レビュー完了後に`discard`で破棄する
  形で実装する。
- ディスク上限の実際の値・GCの実行タイミング(常駐watchモードでの定期実行等)はCLI(M1-10/M1-11)
  側の組み立てで決定する。
- 将来Linux/Docker上への移行(M3-4)は、本ADRのProtocolと「bare clone + MR単位worktree」という
  モデル自体を変えずに、実装(`GitWorkspaceManager`相当)を差し替える形を想定する。
