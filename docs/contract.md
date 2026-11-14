# Conservative address linkage v1

离线整理人员提供 records 数组；每条恰含 id、registry_id、city、postal、street、building、unit。
id 唯一且不做规范化；registry_id 为可空的不透明官方身份字符串。其他必填文本最多256字符、无控制字符。
unit 可空，表示未知，不等于整栋或任意单元。空数组有效，最多100000条。

城市、邮编、街道、楼号、单元执行 Unicode NFKC、casefold、标点转空格、压缩空白。
不推断国家、不做缩写展开、音译、数字前导零删除、地理编码或模糊距离匹配。
相同非空 registry_id 或全部五项地址相同且 unit 已知时连接；同一键以最小ID为星形证据边，
连通分量用最小源ID作 group_id。输出所有规范化源记录、边原因与分组。

分组包含多个官方ID或多个规范化地址断言时标 review，不能自动合并为已确认实体。
无冲突多记录组为 linked，单记录为 singleton；linked 也只是此规则的建议，不代表真实住户核验。
样例6行→4组：A/B linked，C/D conflicting_registry_ids，E、F各自singleton。
组、成员、边顺序由源ID决定，输入排列不改变结果；不删除或覆盖原始字段。

## 人工裁决（可选 --decisions）

裁决文件是仅含 `same`、`different` 两个键的 JSON 对象，值均为源ID二元组数组；二元组无序，
内部规范化为字典序。未知ID、自配对（两个ID相同）、重复或反向重复的裁决、同一对同时声明
same 与 different，一律拒绝（退出2，旧报告不变）。

处理顺序固定：

1. 先对全部 same 裁决求传递闭包（并查集）。
2. 任一 different 对的两个ID落入该闭包同一组时整批拒绝，不写报告。
3. 候选证据边按 `(reason,left,right)` 排序后逐条尝试：合并后若没有任何 different 对同组则
   接纳；否则跳过该边，并在审计中记录该组对之间字典序最小的阻挡ID对 `blocked_by`。
   阻挡关系用组间邻接表维护并随合并 small-to-large 重标，不枚举组内成员笛卡尔积，
   十万记录级别为近线性复杂度。
4. 人工 same 不消除官方ID冲突或地址断言冲突：合并后的组仍按上节口径标 review，
   review_reasons 不做任何豁免。

提供裁决时报告保留全部规范化源记录，并额外输出 `decision_audit`：规范化后的 `same`、
`different` 对（字典序、去重），以及每条候选边的 `accepted`/`blocked` 审计；
`evidence_edges` 只保留被接纳的边。组ID仍取最小成员ID，所有数组与审计顺序确定，
记录或裁决输入乱序不改变结果。未传 `--decisions` 时输出结构与旧契约完全一致，
不含 `decision_audit` 字段。

当前没有跨批次持久ID、概率模型或增量索引。未来演进须有实际来源与误合并证据。
跨批次历史见下节 `address-history`（以原样源 ID 为身份，不使用 `group_id`）。

## 导入前质量诊断（`--audit-only`）

`address-link --source S --output O --audit-only`（库 API：`address_link.audit.audit(data)`、
`address_link.pipeline.audit(path)` 与 `address_link.pipeline.run(path, decisions_path=None,
audit_only=True)`）只读源文件并写出诊断报告。它不建候选边、不分组、不规范化输出记录，
也不改变默认模式（含 `--decisions`）的任何结果与字段。`--audit-only` 与 `--decisions`
互斥，同时给出即参数错误（退出2，旧报告不变）。

**整批失败（不写报告，退出2，旧报告保留）**：输入超过 8 MB；JSON 语法错误、重复键或
非有限数字；根不是恰含 `records` 的对象；`records` 不是数组或超过 100000 条。这些在
逐行诊断之前由同一 `io.load` 契约抛出。

**逐行检查**：对 `records` 每个元素收集全部独立问题，不因某行有错就停止。

| 规则码 | 含义 |
| --- | --- |
| `record_not_object` | 行不是 JSON 对象（其余行级检查跳过） |
| `missing_fields` | 缺少 `id`、`registry_id`、`city`、`postal`、`street`、`building`、`unit` 中的键 |
| `unexpected_fields` | 出现七个契约字段以外的键 |
| `<field>_empty` | 字符串去空白后为空，或不可空字段（`id`、四项地址）为 JSON null；`registry_id`/`unit` 的 null 合法 |
| `<field>_not_text` | 该值不是字符串 |
| `<field>_too_long` | 字符串长度超过 256 个码点 |
| `<field>_control_char` | 含码点小于 32 的控制字符 |
| `<field>_empty_normalized` | 仅 `city`/`postal`/`street`/`building`/`unit`；去空白后原文非空，但按链接契约的 NFKC、casefold、标点转空格、压缩空白后为空（如纯标点、软连字符）；可与 `_too_long`、`_control_char` 同时出现 |
| `id_duplicate` | 与其他行共享完全相同的原始 `id` 字符串 |

值级检查彼此独立：一个值可同时命中 `_too_long` 与 `_control_char`。去空白后为空的字符串
只记 `_empty`，不再重复记 `_empty_normalized`（两者是同一底层问题）；去空白后非空即评估
`_empty_normalized`，因此该码可与 `_too_long`、`_control_char` 同时出现。`registry_id`
与 `id` 是不透明字符串，不做 NFKC 或大小写折叠；`id` 也不做规范化，重复按精确字符串
判定，每个涉及的行（含首次出现）都标记 `id_duplicate`。`unit` 允许 null（未知单元），
不记 `unit_empty`。

**报告结构**（默认按 `sort_keys` 输出）：

- `record_count`：源 `records` 行数，包括有问题的行。
- `valid_count`：无任何规则码的行数。
- `problem_count`：至少有一个规则码的行数；`valid_count + problem_count = record_count`。
- `rule_counts`：规则码到覆盖行数的映射（同一行中同一码只计一次，因此其和可大于
  `problem_count`）；无问题时为空对象。
- `rows`：只含有问题的行，按源数组下标升序。每项为 `{"index": 下标, "rule_codes": [...]}`，
  `index` 为源数组下标（0 起，不是 ID 排序位置），`rule_codes` 在该行内按字典序去重排列。
  无问题时 `rows` 为空数组。

行与码的顺序只由源数组下标和码字符串决定，与输入 ID、map 顺序或重新排列无关；同一
输入字节的重复运行报告逐字节一致。诊断输出与普通报告一样经同目录临时文件原子替换；
读取或写入失败退出2 且旧报告原样保留。该模式只诊断单行契约，不判断跨行语义（除重复
ID 外），不推断国家、不做模糊匹配，也不替代正式链接的冲突裁决。默认模式与有效数据的
输出结构、字段和排序均不变。

## 历史维表（`address-history`）

`address-history` 把正常归并报告中的 `normalized_records` 持久化为 SQLite 历史维表，
并以 `as-of` 导出任意已提交版本的快照。它只消费 `normalized_records`；其他报告字段
（`groups`、`evidence_edges`、`decision_audit`）不参与持久化。`--audit-only` 的诊断报告
不含 `normalized_records`，因而不被接受。报告读取沿用 `io.load` 的整批契约（最多 8 MB、
拒绝重复键与非有限数字）。

### 输入契约

只接受**正常归并报告**（`address-link` 的默认输出或带 `--decisions` 的输出）：

- 报告必须是 JSON 对象且含 `normalized_records` 数组；
- 出现诊断报告标志键（`valid_count`/`problem_count`/`rule_counts`/`rows`）即拒绝；
- 每条记录恰含 `id`、`registry_id`、`city`、`postal`、`street`、`building`、`unit`、
  `normalized` 八个字段；`normalized` 恰含 `city`、`postal`、`street`、`building`、`unit`；
- `id` 为非空文本（最多 256 字符、无控制字符）且全批唯一（精确字符串，不做规范化）；
  `registry_id` 为 `null` 或原样非空文本；`city`/`postal`/`street`/`building` 为必填文本；
- 四条地址字段与 `unit` 的规范化值必须与报告给出的 `normalized` 一致，即重新执行
  NFKC、casefold、标点转空格、压缩空白后逐项相等；地址项规范化后不得为空，`unit`
  可为 `null`，但非 `null` 时规范化后不得为空。

任一条不满足即整批拒绝，不创建、不修改数据库（退出2）；空 `normalized_records` 是
合法的空批次。

### 数据库与身份

SQLite 使用两张表：`history_version(version, projection)` 记录已提交版本及其确定性
六字段投影，`history_interval` 保存区间行。**持久身份是原样源 ID**（`id` 原文，不做
规范化）；`group_id` 不落库、不作身份，导出也不含它。每条记录的六字段为
`registry_id`（原样）加规范化后的 `city`、`postal`、`street`、`building`、`unit`。

### 版本与区间

- 版本必须是正整数（`bool` 与字符串拒绝），新版本严格大于当前最大已提交版本；
- 版本区间左闭右开：`version_from <= v < version_to`，开放区间在 `version_to` 存 `NULL`；
- 导入版本 `v` 时，对每条记录：不存在开放区间则新开 `[v, NULL)`；六字段变化则关闭旧
  区间（`version_to = v`）并以新值新开 `[v, NULL)`；六字段未变则不写行、不新开区间；
- 某 ID 在快照中消失则以 `v` 关闭其开放区间；重现时以重现版本新开区间，不复用旧行；
- 空批次有效：关闭全部开放区间，版本仍登记且可 `as-of` 导出空 `records`；
- 版本号允许有空洞；查询版本必须已登记才可导出，`as-of` 不做区间推断。快照完全由
  区间谓词 `version_from <= v AND (version_to IS NULL OR version_to > v)` 决定。

### 幂等与冲突

同一版本重复导入：先把该版本的六字段按 ID 排序序列化为规范投影，逐字节比较。
投影相同则幂等（不再写行，返回 `idempotent: true`）；不同则拒绝（退出2，已提交状态
不变）。记录在报告数组中的顺序不影响投影或身份。

### 事务

校验（版本、报告结构、字段、唯一 ID、规范化一致性）全部发生在打开数据库之前。
新版本的区间关闭/开启与 `history_version` 登记在同一 `BEGIN IMMEDIATE` 事务中，
任一步失败整体 `ROLLBACK`：不留下版本登记，也不留下部分区间修改。`as-of` 以只读
模式打开数据库，不写文件。

### CLI

```sh
address-history import --database history.sqlite3 --report report.json --version 1
address-history as-of  --database history.sqlite3 --version 1 --output snapshot.json
```

`import` 成功时向 stdout 打印 `{"version", "record_count", "idempotent", "opened", "closed"}`。
`as-of` 导出 `{"version", "record_count", "records"}`，`records` 按原样 `id` 升序，
每项恰含 `id` 与六字段。未知版本、数据库不存在或无效参数退出2；`as-of` 通过同目录
临时文件原子替换写出，失败时已有输出文件原样保留。库 API 为
`address_link.history.import_report(database, report_path, version)` 与
`address_link.history.as_of(database, version)`。

## 配送结算事件事实（`delivery-facts`）

`delivery-facts` 把配送结算事件按事件版本归属到 `address-history` 维表，固化当时有效的
六字段属性，写入**独立**的 SQLite 事实库；`summary` 按 `city`、`postal` 排序汇总事件数
与分币金额。事实库与历史库完全分离，源维表只读。

```sh
delivery-facts ingest  --database facts.sqlite3 --events events.jsonl --history history.sqlite3
delivery-facts summary --database facts.sqlite3 --output summary.json
```

库 API：`address_link.delivery.ingest(database, events_path, history_database)` 与
`address_link.delivery.summary(database)`。

### 事件输入

`ingest` 读取 JSONL：每行一个 JSON 对象，恰含 `event_id`、`source_id`、`version`、
`cents` 四个字段；空行（含末尾换行）忽略，其余每行必须是完整对象。JSON 解析沿用
`io.loads` 的契约（拒绝重复键与非有限数字），错误信息带 1 起的行号。最多 100000 个
非空事件行。

- `event_id`、`source_id`：沿用 `io.text` 的原样文本规则——非空（去空白后非空）、最多
  256 码点、无码点小于 32 的控制字符；不做 NFKC 或大小写折叠。
- `version`：正整数，`1 <= version <= 2**63-1`；`bool`、浮点与字符串拒绝。
- `cents`：有符号 64 位整数，`-2**63 <= cents <= 2**63-1`；`bool`（`true`/`false`）、
  浮点、字符串与越界整数拒绝。

任一行不满足即整批拒绝（退出2，事实库不变）。`source_id` 是原样文本，与维表的持久
身份 `id` 精确匹配。

### 归属与快照

- 入库前以**只读**方式打开历史库（`mode=ro`），在单个读事务中读取已登记版本集合，
  并一次性取出整批涉及的源 ID 区间；整批观察同一已提交快照，不写源库。
- 每条事件用其 `version` 在对应 `source_id` 的区间中二分定位
  `version_from <= version < version_to`，取该区间固化的六字段
  （`registry_id`、`city`、`postal`、`street`、`building`、`unit`）。
  不逐事件扫描全部区间。
- 事件的 `version` 必须是历史库已登记的版本；否则整批拒绝。
- `source_id` 在该版本必须存在（存在覆盖该版本的区间）；否则整批拒绝。
  不存在、尚未出现或已消失都按“当时不存在”处理。
- 若维表对某个 `source_id` 存在重叠区间（如被外部篡改），归属不唯一，整批拒绝。
- 固化的六字段写入事实行，之后维表演进不影响已结算事实。

### 事实库

SQLite 表 `delivery_fact(event_id, source_id, version, cents, registry_id, city,
postal, street, building, unit)`，`event_id` 为主键。事件的读取、校验与归属都在打开
事实库之前完成，失败时不创建事实库；写入阶段的失败整体回滚，不留下任何事实或半批。

### 幂等、冲突与事务

- `event_id` 全局唯一，唯一性作用于整库，不限于单批。
- 同 `event_id` 且固化内容（`source_id`、`version`、`cents` 与六字段）完全一致视为重投，
  幂等跳过并计入返回的 `idempotent` 数。
- 同 `event_id` 但内容不同视为冲突：整批拒绝（退出2），已提交事实不变。批内重复使用
  相同 ID 但内容不同属于冲突；内容相同则在批内先去重。
- 所有待插入事实在单个 `BEGIN IMMEDIATE` 事务中提交；任一步失败整体 `ROLLBACK`，
  不留下部分事实，也不留下半批。
- `ingest` 成功时向 stdout 打印 `{"event_count", "inserted", "idempotent"}`：
  `event_count` 为批内去重后的事件数，`inserted` 为新写入行数，`idempotent` 为跳过的
  重投数。

### 汇总

`summary` 以只读方式读取整张事实表，按固化后的 `city`、`postal`（均为规范化文本）分组，
按 `(city, postal)` 升序输出：

- `event_count`：库内事实总数；
- `cents`：全部事实的分币总额；
- `group_count`：分组数；
- `groups`：每项 `{"city", "postal", "event_count", "cents"}`，按 `(city, postal)` 升序。

金额以 Python 整数逐行累加，结果精确，可超过有符号 64 位而不溢出或丢精度。
`summary` 通过同目录临时文件原子替换写出，失败时已有输出原样保留。事实库或事实表
不存在、未知参数退出2。

### CLI

```sh
delivery-facts ingest --database F --events E --history H
delivery-facts summary --database F --output O
```

`ingest` 要求 `--database` 与 `--history`、`--events` 均不同；`summary` 要求
`--output` 与 `--database` 不同。两者失败均退出2。
