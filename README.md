# caldesk —— 合用接待室的预约台与外借匿名日历

两家虚构咨询室合用几间接待室。当事人地址、主题、与谁约见只留在各自馆内；
受托预约台只能取得**房间占用**和**自己名下预约的改约能力**。

本项目用纯 Python 标准库实现，并以测试逐条兑现题面要求。

## 隐喻 → 实现对照

| 题面表述 | 实现位置 |
| --- | --- |
| 馆内能导入 iCalendar 的周期事件、按原实例改时的例外和取消项 | `caldesk/ical.py`：RRULE 主事件、`RECURRENCE-ID` 改时 VEVENT、`STATUS:CANCELLED` + `EXDATE`，读取器可完整导回（`tests/test_icalendar.py`） |
| 交出在另一账户确能打开的外借日历 | `ExternalLender.export()`：标准 VCALENDAR、全 UTC、无 VTIMEZONE 依赖，读取器可展开为普通事件 |
| 只可取得房间占用与自己名下改约权 | `BookingEngine.room_busy()` 只回区间；`reschedule_instance/cancel_instance` 校验 `series.owner == desk_id`，越权即 `BookingError` |
| 两套内档只换私人标题或同段互换客人，外借件无法分辨 | 外借件仅由“房间+起止时间”哈希出稳定 UID；`test_private_title_or_guest_swap_is_indistinguishable` 断言逐字节相同、通知序列相同 |
| 稳定标识、例外条数、更新通知也不能分辨 | 外借件无 RRULE/RECURRENCE-ID（例外条数恒为 0）；纯私人改动不产生通知（`NoticeBoard`） |
| 按预约期重建只含匿名占用的连续区段（方案一） | `ExternalLender._merge()` 合并相交/背靠背区间，每段一个匿名 VEVENT，标题恒为“占用” |
| 具名改约留给独立经办回执 | `model.Receipt`：含客人、动作、费用，仅存馆内档案，不进外借件 |
| 按日发与可调整余量绑定的房间报价，报价期内兑现 | `policy.make_quote()` + `BookingEngine.place_hold()`：24h 有效期内锁定余量，过期才释放 |
| 把周期和临时改约的代价告诉用户 | `python -m caldesk.cli cost`，规则见 `caldesk/policy.py` |
| 无障碍房 + 连续两次同一口译员，两场都订妥再通知 | `BookingEngine.book_two_slots()`：原子提交，任一失败全部回滚；口译员占用独立登记 |
| 跨一次夏令时切换，周期挂钟时间不变 | `America/New_York` + 规则化 `VTIMEZONE`；10-21 10:00 输出 14:00Z（EDT），11-04 10:00 输出 15:00Z（EST） |
| 把一次挪到与另一实例同日；只撤一个原实例，检查另一单 | `test_move_one_instance_onto_same_day_then_cancel_one_original`：取消以 UID+RECURRENCE-ID 精确命中，南馆 14:00 一单照常可入住 |
| 还原馆内材料，只改口译员已占的半小时；已购房间不可重复占用 | `test_restore_archive_then_only_move_interpreters_half_hour`：重叠预订被拒，平移到空闲半小时后成功，原售出占用保留 |

## 代价规则（方案一）

- 新周期预约：提前 **7 天**受理，免改约费；不足 7 天转临时预订。
- 周期内单实例改时/取消：提前 **48 小时免费**（每实例一次）；
  24–48 小时收半日房费 300；不足 24 小时收全日房费 600。
- 临时预订：提前 24 小时标准价；不足 24 小时加收 50%。
- 报价有效期 24 小时，期内锁定余量按报价兑现。

## 使用

```bash
python -m caldesk.cli cost                  # 查看代价规则
python -m caldesk.cli demo sample_calendars # 跑通完整情节并写出 ICS
python -m unittest discover -s tests -v     # 18 个测试
```

## 目录

```
caldesk/
  time_utils.py   时区、夏令时 VTIMEZONE、WEEKLY 展开、RFC5545 折行
  ical.py         iCalendar 读取（RRULE/例外/取消/EXDATE）与写出
  model.py        Room / Series / Override / Receipt / InternalArchive
  booking.py      占用冲突、改约权、报价锁定、原子两场预订
  external.py     外借匿名连续区段日历与防泄露自检
  policy.py       周期/临时改约代价与报价
  notices.py      外借日历更新通知板
  scenario.py     题面完整情节
tests/            18 个测试，覆盖上述每一条
sample_calendars/ demo 落盘的 5 份 ICS
```

## 设计要点

1. **信息边界**：引擎只持有房间忙闲与 owner 判定，外借器写出前做
   `assert_leaks_no_private_data` 自检（人名、标题、RRULE、ATTENDEE、
   RECURRENCE-ID 一律不得出现）。
2. **不可分辨性**：外借 UID = `sha256(房间|UTC起|UTC止)`，与馆内 UID、
   客人、标题无关；相同占用输入必然产生逐字节相同的外借件。
3. **时间语义**：内部一律本地挂钟时间 + 固定时区，写出时才转 UTC，
   因此夏令时切换后“每周三 10 点”仍是本地 10 点。
4. **原子性**：两场口译请求先暂存、全部成功才落地；失败时口译员占用
   与房间预订一起回滚。
