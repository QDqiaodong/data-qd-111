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
