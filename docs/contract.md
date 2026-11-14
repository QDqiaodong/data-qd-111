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
