# task26100281

IEP 服务履约核算库(纯 Python 标准库,单进程,内存存储)。

- `iep_ledger.py`:`Ledger` 提供 `record_mandate` / `record_session` / `report(month, as_of)` 三个操作。
- 口径:按周履约(校区时区 America/New_York,当地周一零点起)、按工作日归属月份;应履约与计入分钟以 `Fraction` 精确表示,不足线为严格低于 85%。
- 修订按 `recorded_at` 选版,与加入顺序无关;作废版视为不存在。

运行测试:

```sh
python3 -m unittest test_iep_ledger -v
```
