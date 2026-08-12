# Spike S-2: 社内GitLab REST API の疎通・仕様確認

- 対応Issue: [#26](https://github.com/AtsushiNi/gitlab-ai-platform/issues/26)
- ステータス: **公式ドキュメント調査のみ完了 / 実機疎通は未検証**
- 最終更新: 2026-08-12

## 1. 検証の前提と制約

このSpikeは本来「社内GitLabへの実疎通」を目的とするが、今回の調査はサンドボックス環境から
社内GitLabへネットワーク到達できず、接続用のURL・PATも用意されていなかったため実施できなかった。

代わりに、GitLab公式REST APIドキュメント(docs.gitlab.com、2026-08時点)をもとに、
Adapter(M1-1, M1-2)の設計に必要な仕様を先に確定させる。**実機固有の値(実際のバージョン、
組織のレート制限設定、PATの発行可否など)は別途、実機での確認が必須**。未検証項目は §7 にまとめた。

## 2. GitLabバージョンの確認方法

- `GET /api/v4/version` … `{ "version": "x.y.z-ee", "revision": "..." }`
- `GET /api/v4/metadata` … version に加え `enterprise`(EE/CE判定)、`kas`(Kubernetes Agent Server)情報などを含む上位互換のエンドポイント

いずれも認証必須(`PRIVATE-TOKEN` ヘッダ)。Adapter初期化時にどちらかを叩き、
バージョンを取得・ログ出力しておくと、後述のAPI仕様差異(deprecated endpoint等)の切り分けに使える。

**実機で確認すべきこと**: 社内GitLabのバージョンとEdition(CE/EE)。バージョンによっては
本ドキュメントで前提にしている一部エンドポイント・パラメータ(特にkeyset pagination対応状況や
`changes` エンドポイントの扱い)が使えない可能性がある。

## 3. 認証・PATスコープ

主要スコープ(個人アクセストークン):

| スコープ | 内容 |
|---|---|
| `api` | APIへの完全な読み書きアクセス。MR作成・コメント投稿など書き込み操作にはこれが必要 |
| `read_api` | APIへの読み取り専用アクセス |
| `read_repository` | Git経由のリポジトリ pull のみ |
| `write_repository` | Git経由のリポジトリ pull/push(`read_repository`を包含) |
| `read_user` | 認証ユーザープロフィールの読み取りのみ |

**Adapterへの示唆(M1-3の許可リスト機構と対応)**:

- 読み取り専用の処理(MR一覧・詳細・diff・コメント取得)は `read_api` のみで完結できる
- ブランチ作成・push・MR作成・コメント投稿(書き込み)には `api` スコープが必要
  ( `write_repository` はGit経由のpushのみをカバーし、REST APIでのMR作成・コメント投稿はカバーしない)
- **`api` スコープには常に読み書き全体が含まれ、GitLabのスコープ機構だけでは
  「MR作成・コメントは許可するがmergeは禁止」という粒度の制御はできない**。
  マージの可否はスコープではなくプロジェクト内のロール(Maintainer以上)で制御される仕組みのため、
  M1-3(書き込み操作の許可リスト機構)は **PATスコープに依存せず、Adapter層のコード側で
  呼び出せるAPI操作を機構的に絞り込む**設計が必須(現状の設計方針どおりで正しい)。
  加えて、AI用アカウントのロールをMaintainer未満(Developer等)に留めることで、
  仮にAdapter層の制御をすり抜けてもGitLab側でmergeが拒否される二重の防御にできる(M3-8と関連)。

## 4. ラベルによるMR検索

```
GET /projects/:id/merge_requests?labels=レビュー待ち
GET /merge_requests?labels=レビュー待ち  (認証ユーザーが所属する全プロジェクト横断)
```

- `labels` はカンマ区切りで複数指定可、大文字小文字は区別されない
- `labels=None` で「ラベルなし」、`labels=Any` で「何かしらラベル付き」を取得可能
- 追加の絞り込みに `state=opened`、`updated_after` / `updated_before`(ISO 8601)、
  `created_after` / `created_before`、`scope`(`created_by_me` / `assigned_to_me` / `all`)などが使える

**Poller(M1-5)への示唆**: プロジェクト横断で「`レビュー待ち`ラベルかつopened」を1回のリクエストで
取得できる(`/merge_requests?labels=...&state=opened&scope=all`)。ただし対象プロジェクトを
限定したい場合はプロジェクト単位のエンドポイントをプロジェクト数分呼ぶ必要がある。
対象プロジェクト数と後述のレート制限を踏まえてどちらの方式にするかを実機検証時に決める。

## 5. MR詳細・diff・コメントの取得

| 用途 | エンドポイント | 備考 |
|---|---|---|
| MR詳細 | `GET /projects/:id/merge_requests/:iid` | `title`/`description`/`state`/`labels`/`author`/`diff_refs`/`sha` などを含む |
| 差分(diff) | `GET /projects/:id/merge_requests/:iid/diffs` | 推奨エンドポイント。ページネーション対応 |
| 差分(旧) | `GET /projects/:id/merge_requests/:iid/changes` | **GitLab 15.7でdeprecated、API v5で削除予定**。新規実装では使わない |
| コメント | `GET /projects/:id/merge_requests/:iid/notes` | 単発コメント一覧 |
| スレッド | `GET /projects/:id/merge_requests/:iid/discussions` | コメントのスレッド構造(返信関係)込み。レビューコメントの文脈を追うにはこちらが適切 |

**Adapter(M1-2)への示唆**: diff取得は `changes` ではなく `diffs` を採用する。
コメントは「議論の文脈」を保つため `discussions` を第一候補にし、必要に応じて `notes` を併用する。

## 6. ページネーション

- **Offset pagination(デフォルト)**: `page`(既定1) / `per_page`(既定20、最大100)。
  レスポンスヘッダに `X-Total`、`X-Total-Pages`、`X-Next-Page`、`X-Prev-Page` および `Link` ヘッダ
- **Keyset pagination**: `pagination=keyset` + `order_by` + `sort` を指定。大規模コレクションで
  GitLab公式が推奨する方式。`Link` ヘッダの `next` URLを辿る形でカーソル移動する

**Adapterへの示唆**: MR一覧はプロジェクト規模次第で件数が伸びるため、`per_page=100` を基本にしつつ、
`X-Next-Page` の有無でループ終了を判定する実装にする。プロジェクト数・MR数が多い場合はkeyset方式への
切り替えも検討(実機のGitLabバージョンがkeyset対応か要確認)。

## 7. レート制限

- **セルフマネージドGitLabはAPI全般のレート制限がデフォルトで無効**(GitLab.comとは異なる)。
  管理者が `Admin > Settings > Network > User and IP rate limits` で有効化しない限り制限はかからない
- 有効化した場合のデフォルト値の目安: 未認証(IPごと) 3600req/h、認証済み(ユーザーごと) 7200req/h
- 制限に達すると `429` + プレーンテキストボディ、`RateLimit-Limit` / `RateLimit-Remaining` / `Retry-After` ヘッダ

**Adapterへの示唆**: 社内GitLab側で制限が有効化されているか不明なため、有効/無効どちらでも壊れないよう
`429` ハンドリング(`Retry-After` に従ったバックオフ)は最初から実装しておく
(M1-2の「リトライとエラー分類」に含める)。

## 8. Adapter設計への反映まとめ(M1-1 / M1-2 / M1-3への申し送り)

1. 初期化時に `/version` (or `/metadata`) を叩いてバージョンを記録し、非対応エンドポイントの
   切り分けに使えるようにする
2. 読み取り操作(一覧・詳細・diff・コメント)は `read_api` スコープのPATのみで動作するように設計する
   → 誤って書き込み系コードパスに `api` スコープの強い権限が要求されないよう、読み取り専用の
   Adapter実装は `read_api` トークンでも通しテストできる構成にする
3. diffは `changes` ではなく `diffs` エンドポイントを使う
4. コメント取得は `discussions` を基本にする
5. 一覧取得は `per_page=100` + `X-Next-Page` ベースのoffsetページングをまず実装し、
   keysetは実機バージョン確認後に検討
6. `429` を含むリトライ・エラー分類を最初から組み込む
7. **書き込み操作の許可リスト(M1-3)はPATスコープに委譲できない**。Adapter層のコードで
   呼び出し可能なAPI操作そのものを絞り込む実装が必須。加えてAI用GitLabアカウントのロールを
   Maintainer未満にすることで二重に防御する(M3-8で正式検討)

## 9. 未検証事項(実機での確認が必須)

- [ ] 社内GitLabの実バージョン・Edition
- [ ] 社内GitLabでAPIレート制限が有効化されているか、有効ならその値
- [ ] 発行可能なPATの種類・スコープ制限(組織ポリシーで `api` スコープ自体が禁止されていないか)
- [ ] AI用アカウントを新規に用意できるか、ロールをDeveloper等に制限できるか(M3-8関連)
- [ ] keyset paginationが実機バージョンで利用可能か
- [ ] 対象プロジェクト数・MR数の規模感(横断検索 vs プロジェクト別リクエストの選択に影響)
- [ ] 実際のレスポンスタイム(ポーリング間隔30〜60秒が妥当か)

## 参考資料

- [Merge requests API](https://docs.gitlab.com/api/merge_requests/)
- [REST API resources](https://docs.gitlab.com/api/rest/)
- [REST API deprecations and removals](https://docs.gitlab.com/api/rest/deprecations/)
- [Metadata API](https://docs.gitlab.com/api/metadata/)
- [Access token scopes](https://docs.gitlab.com/security/tokens/access_token_scopes/)
- [Personal access tokens](https://docs.gitlab.com/user/profile/personal_access_tokens/)
- [User and IP rate limits](https://docs.gitlab.com/administration/settings/user_and_ip_rate_limits/)
