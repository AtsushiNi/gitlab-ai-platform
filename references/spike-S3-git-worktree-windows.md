# Spike S-3: Windows での git worktree 運用検証

- 対応Issue: [#27](https://github.com/AtsushiNi/gitlab-ai-platform/issues/27)
- ステータス: **メカニズム検証は完了(macOS上の合成リポジトリ) / Windows実機・実際の社内リポジトリでの再検証が必須**
- 最終更新: 2026-08-12

## 1. 検証の前提と制約

このSpikeは本来「Windows環境での実運用」を検証対象とするが、作業環境がmacOSのため、
Windows実機での検証はできなかった。そのため以下の方針で代替した。

- **git自体の挙動(bare clone / worktree / 並列実行 / 認証機構)**は、OS非依存の部分が大半のため、
  macOS上に用意した合成リポジトリで実際にコマンドを実行し、実測した(§3〜§6, §8)
- **Windows固有の制約(パス長制限)**は実機で再現できないため、文献知見と実際にありそうな
  パス構成の文字数計算で代替した(§7)。**実機Windowsでの再検証が必須**
- 認証は社内GitLabへの実接続ができないため、`git credential fill` や `core.sshCommand` といった
  **gitの認証委譲の仕組みそのもの**が期待通り動くかを検証した。実際のPAT/SSH鍵での認証成否は未検証(§8, §9)
- Git for Windows の Git Bash は portable な OpenSSH クライアントと git.exe(msys2ベース)を
  同梱しており、`core.sshCommand` や `credential.helper` の呼び出し規約はUnix版gitと同一のため、
  §3〜§6, §8 の結論はWindowsへの転用可能性が高いと考えられる(が、実機確認までは仮説)

検証に使った合成リポジトリ: 50モジュール×3ファイル(計150ファイル、.git約3.5MB)、
`main` + `mr-101`/`mr-102`/`mr-103` の3ブランチ。手順は末尾に残していないが、
`git init` → ファイル生成 → `git clone --bare` → `git worktree add` という素朴な構成。

## 2. 検証項目と結論の要約

| 項目 | 結論 |
|---|---|
| bare clone + MR単位worktree | 問題なく動作。objectは共有され、worktreeごとの追加コストは作業コピー分のみ |
| 並列worktree作成 | 複数worktreeを同時に`add`しても競合しない |
| bare репо への同時fetch | **落とし穴あり**: `refs/heads/*` を直接更新するfetchは、該当ブランチが別worktreeでcheckout中だと拒否される。`refs/remotes/origin/*` 経由にする必要がある(§4) |
| worktreeの異常系 | ディレクトリを直接消してもGit管理上は壊れず、`git worktree prune` で復旧可能 |
| パス長制限(Windows) | 実在しそうな構成で260文字(MAX_PATH)を超えるケースを確認。設計で回避が必要(§7) |
| 認証(PAT) | 環境変数由来のcredential helperでトークンをディスクに残さず供給できることを確認(§8) |
| 認証(SSH) | `core.sshCommand` でリポジトリ単位にBot専用鍵を強制できることを確認(§8) |
| 大きめリポジトリでの所要時間・ディスク | 今回は実測しなかった(§0参照)。gitのアーキテクチャ上の性質から見積もりを記載(§6) |

## 3. bare clone + MR単位worktree

```
git clone --bare <origin> bare.git
git worktree add worktrees/mr-101 mr-101
git worktree add worktrees/mr-102 mr-102
git worktree add worktrees/mr-103 mr-103
```

3ブランチとも問題なく作成でき、`git worktree list` で全worktreeのパスとHEADが確認できた。
worktreeはそれぞれ独立した作業ディレクトリ・indexを持つため、**並列レビュー間で作業ツリーが
衝突することはない**(タスク整理.mdのM1-6の前提どおり)。

## 4. 並列実行

### 4.1 worktree作成の並列実行

3本の`git worktree add`を同時に(バックグラウンドジョブとして)実行しても、全て正常終了し
ロック競合は発生しなかった。`git worktree add` はbare repo側の共有ロックをほぼ取らない設計のため、
**MR単位の並列レビュー開始(=並列worktree作成)は素直に並列化してよい**。

### 4.2 【重要な発見】bare repoへの同時fetchで発生する落とし穴

再レビュー時、Workspace Managerがbare repoに対して新しいコミットを取り込む(`git fetch`)
タイミングで、対象ブランチが別のworktreeでcheckout中だと以下のように**fetch自体が拒否される**:

```
fatal: refusing to fetch into branch 'refs/heads/mr-101' checked out at
'.../worktrees/mr-101'
```

これは `git fetch origin '+refs/heads/*:refs/heads/*'` のように、リモートのブランチを
**bare repo自身のブランチ参照に直接上書き**しようとした場合に起きる。worktreeで
checkout中のブランチをこの方法で更新することをgitが安全のため拒否するため。

**回避策(検証済み)**: bare repo側は `refs/remotes/origin/*` という別名前空間にだけfetchし、
`refs/heads/*` には触れないようにする。

```
git fetch origin '+refs/heads/*:refs/remotes/origin/*'
```

この方式なら、他のworktreeで読み取り操作(`git status` / `git diff` / `git log`)が
同時に走っていてもfetchは正常終了する(実測: 3並列で衝突なし)。

各worktree側は、対象ブランチの最新化が必要になったタイミングで自分自身の中で
`git fetch && git reset --hard origin/<branch>` (または `git pull`)を行う。
これは他のworktreeやbare repoの状態に影響しない、worktreeローカルな操作。

**M1-6(Workspace Manager)への反映**: bare repoに対する定期fetchは
`refs/heads/*:refs/remotes/origin/*` のリモートトラッキング方式を採用する。
`refs/heads/*:refs/heads/*` のような直接上書き方式は、稼働中の並列レビューがある限り
いずれ確実に失敗するため使用しない。

## 5. worktreeの破棄・異常系

- `git worktree remove --force <path>` で正常に破棄・GCされる
- worktreeディレクトリを`rm -rf`で直接消してしまった場合(異常終了やクラッシュを想定)、
  bare repo側には「prunable」なエントリとして残るだけで壊れず、
  `git worktree prune` で復旧できる
- **M1-6への反映**: Workspace Managerの起動時ヘルスチェック/GC処理に
  `git worktree prune` を定期実行する項目を含めるとよい(異常終了からの自己復旧)

## 6. ディスク使用量・所要時間について(実測はスコープ外)

実際に大きめの公開リポジトリをダウンロードして計測することも検討したが、以下の理由で
今回は見送った(実装判断としてはこの結論で十分と判断):

- worktreeのディスク増分は「objectはbare repoと共有し、worktreeごとに増えるのは
  そのHEADの作業コピー相当分のみ」というgitのアーキテクチャ上の性質であり、
  検証(§3〜§5)で実際にそのとおりの挙動(bare repoの増分は数百KB程度のメタデータのみ、
  worktree側は作業コピーサイズ相当)を確認済み
- clone/fetchの所要時間はリポジトリの履歴・オブジェクト量にほぼ比例するという一般的な性質であり、
  社内リポジトリと無関係な公開OSSリポジトリの絶対値を測っても、タイムアウト設計や
  ディスク容量見積もりの参考にはなりにくい
- **実際の所要時間・ディスク使用量は、対象となる社内GitLabリポジトリで実測すべき**
  (§9の未検証事項に記載)

## 7. パス長制限(Windows MAX_PATH)

Windowsの伝統的なパス長制限は260文字(`MAX_PATH`)。bare clone + worktree構成は
ベースパスに `worktrees/mr-<iid>-<branch-slug>/` という階層をさらに追加するため、
GitLabのグループ/サブグループ階層や長いブランチ名、`node_modules`のような深いネスト構造を持つ
プロジェクトでは実際に問題になりうる。以下、実在しそうな構成での文字数試算:

| 構成 | 文字数 | 260文字制限 |
|---|---|---|
| 最短構成(`C:\ws\proj\wt\mr-1234\src\index.ts`) | 34 | 余裕 |
| 素朴な構成(ユーザー名+グループ/サブグループ+プロジェクト名+ブランチslug+Java深階層) | 227 | ギリギリ未満 |
| 上記+サブグループがもう1階層深い、ブランチ名がやや長い | 294 | **超過** |

**対策(文献調査ベース、実機未検証)**:

1. **`git config --global core.longpaths true`** — Git for Windows(msys2ベースのgit.exe)は
   このオプションが有効な場合、内部的に`\\?\`プレフィックス付きパスでファイルアクセスするため、
   **Windowsのレジストリ変更(`LongPathsEnabled`、管理者権限が必要)なしに** git自身の
   260文字制限は回避できるとされている。`git config --global`はユーザースコープの設定であり
   管理者権限は不要
2. ただし②を有効にしても、**Claude Code本体やその他のツール(エディタ、antivirus等)が
   同じ深いパスを扱えるとは限らない**。Win32 APIレベルの長パス対応(`LongPathsEnabled`)は
   レジストリ変更が必要でこの環境では利用できない(「管理者権限なし」という前提と矛盾する)
3. そのため設計としては、長パス対応に依存するのではなく、**そもそも深くしない**方針を推奨:
   - ワークスペースのベースパスを短く固定する(例: `C:\ws\` 直下)
   - worktreeディレクトリ名にブランチ名のslugをそのまま使わず、**`mr-<iid>` のような短い
     識別子**にする(ブランチ名はworktree内の`git log`/checkout済みブランチとして参照可能なので、
     ディレクトリ名に含める必要はない)
   - プロジェクト名・グループ名も、必要ならローカルの短いエイリアス/連番にマッピングする

**未検証**: 実際に260文字を超えるパスで `git worktree add` やClaude Codeのファイル読み書きが
Windows上でどう失敗する(あるいはしない)かは、Windows実機での再現確認が必要。

## 8. 認証(PAT/SSH)

社内GitLabへの実接続はできないため、**gitが認証情報の受け渡しに使う仕組み自体**が
非対話・自動実行(Workspace Managerからの無人実行)に適合するかを検証した。

### 8.1 PAT: credential helperによる非対話供給

環境変数からPATを読み、標準出力に`username`/`password`として返すだけの
カスタムcredential helperスクリプトを作成し、`git credential fill` 経由で動作することを確認した。

```sh
# pat-credential-helper.sh (get操作のみ実装)
echo "username=oauth2"
echo "password=${GITLAB_PAT}"
```

```
git -c credential.helper=./pat-credential-helper.sh credential fill
# → username=oauth2 / password=<GITLAB_PATの値> が返る
```

`.git/config` にトークンが書き込まれないことも確認した(`credential.helper=store`は
平文でディスクに残るため避け、この方式のように**プロセスの環境変数から都度供給する**方が安全)。

**M0-2(設定・シークレット管理)/ M1-2への反映**: PATは設定ファイルではなく環境変数
(またはOSのシークレットストア)で保持し、Workspace Managerが起動する git コマンドに対して
上記のようなcredential helperを`-c credential.helper=...`で都度指定する方式を採用する。
GitLab側はHTTPS + PAT認証で `username` は任意の文字列(慣例的に`oauth2`または実際のGitLabユーザー名)
でよく、`password`欄にPATを渡せばよい(GitLab公式ドキュメントの記載どおり)。

### 8.2 SSH: `core.sshCommand` によるリポジトリ単位の鍵固定

偽の`ssh`コマンド(呼び出し引数をログするだけ)を用意し、`git config core.sshCommand`で
リポジトリ単位に指定できることを確認した。実際に `git fetch` 実行時、指定した鍵ファイルパス
(`-i .../bot_ed25519 -o IdentitiesOnly=yes`)付きで正しく呼び出されることをログで確認した。

**M1-2への反映**: 人間の`~/.ssh/config`やデフォルトのSSH鍵に依存せず、
AI用のBot専用SSH鍵をリポジトリ(worktree)単位で`core.sshCommand`により強制できる。
Git for WindowsのGit Bashも同じOpenSSHクライアント実装を同梱しているため、
同じ設定方法が使えると考えられる(実機未検証)。

**PAT vs SSHの選択について**: 無人実行かつ複数プロジェクトを横断するWorkspace Managerの用途では、
SSH agentのように鍵の平文をメモリに常駐させ続ける方式より、**スコープ付きPATを環境変数経由で
都度供給する方式(8.1)の方が運用・失効管理がしやすい**(トークンのローテーションがGitLab側の
UI操作のみで完結し、鍵ペアの再配布が不要なため)。ただしこれは設計方針の推奨であり、
実際にどちらを採用するかはM0-2で正式に決定する。

## 9. 未検証事項(実機での確認が必須)

- [ ] Windows実機での `git worktree add` / `prune` / 並列実行の再現(本SpikeはmacOSでの代替検証)
- [ ] 実際に260文字を超えるパスでの挙動(git / Claude Code / その他ツール)
- [ ] `core.longpaths=true` が実際にWindows上でMAX_PATH制限を回避できるかの実機確認
- [ ] 実際の社内GitLabリポジトリでの clone/fetch/worktree作成の所要時間とディスク使用量
- [ ] 実際のPAT・SSH鍵での認証成否(社内GitLabの認証方式・ネットワークポリシー次第で
      挙動が変わる可能性。特にSSHはポート22の社内ネットワーク許可状況に依存)
- [ ] Git BashからのGit for Windows版gitで、本Spikeで使った`core.sshCommand` /
      `credential.helper` 呼び出し規約が同一に動くことの確認
- [ ] 外部ダウンロード制限下でGit for Windowsそのもの、または追加コンポーネント
      (Git Credential Manager等)の導入可否

## 参考資料

- [git-worktree Documentation](https://git-scm.com/docs/git-worktree)
- [git-credential Documentation](https://git-scm.com/docs/git-credential)
- [git-config Documentation (core.sshCommand, core.longpaths)](https://git-scm.com/docs/git-config)
- [GitLab: Personal access tokens (HTTPS認証時のusername/password規約)](https://docs.gitlab.com/user/profile/personal_access_tokens/)
- Git for Windows 関連: `core.longpaths` によるMAX_PATH回避についての知見
  (Git for Windowsのリリースノート・wikiに基づく一般的知見。本Spikeでは実機未検証)
