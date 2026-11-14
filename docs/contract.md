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

当前没有人工裁决写回、跨批次持久ID、概率模型或增量索引。未来演进须有实际来源与误合并证据。
