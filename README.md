# fwstack — 完全离线的固件栈检查与装板工具

纯 Python 标准库实现，无任何外部依赖、无网络访问。装板员拿到的是一份可在本地
解释器回放的 JSON 配置；未获保证的程序/板卡组合会被明确指出具体缺口，且无法交付。

## 它解决什么问题

一份采样固件包含：

- **普通调用**：每个函数有独立栈帧 `frame`（字节）和自身执行时间 `work`（微秒）；
- **两种互斥运行模式**：模式专用函数只能从本模式入口到达；
- **高低两级中断**：只在 `region` 标记区间或 `irq on` 之后开放，高级可打断低级
  形成嵌套，调用者被打断时原帧保留；
- 板卡列出 **RAM 总量、保留区、采样周期、两级最晚响应时间**。

工具回答：**这份固件能安全装进哪块小板？用哪种栈接法？响应时间还兑不兑现？**

## 两种栈接法（模拟板都支持，配置里二选一）

| 接法 | 峰值构成 | 等待 | 内存代价 |
|---|---|---|---|
| `shared` 共享栈 | 最深前台帧 + 嵌套中断链 | 中断可在可证明的短封闭区间内被推迟 | 只需一段连续栈 |
| `isolated` 隔离栈 | 前台栈与中断栈分区，各自独立峰值 | 中断少等待，嵌套返回走独立栈 | 从固定内存切两个区，受保留区碎片约束 |

内存放置采用穷举搜索（栈区数 ≤ 2），能区分两种失败：
**总容量不足** 与 **总量够但被保留区切成碎片、没有足够大的连续块**。

## 静态分析给出的承诺

- 每个模式的前台栈峰值、前台执行时间、最长封闭区间 `W_L`；
- 每级中断处理峰值、高打断低的嵌套峰值、处理时间 `C_L`；
- 最坏响应上界：
  `R_L = W_L + Σ(更高优先级到达数 × 其处理时间) + C_L`
  （低级要计入高级在本周期内的全部到达，嵌套中断据此计入）；
- 每周期最少占用 ≤ 采样周期；
- 两种栈接法各自需要的总栈量与在空闲块中的具体放置地址。

静态峰值是**安全上界**；本地回放给出某条具体事件列的实测峰值，实测永远不超过上界。

## 目录

```
fwstack/            库代码（model/parser/analyzer/deliver/interpreter/cli）
examples/
  programs/         三份固件描述：基线 / 仅移动开放点的推迟版 / 宽区间版
  boards/           板卡描述：NANO、NANO_P（外设预留）、NANO_F（纯碎片）、MID（紧响应）
  events_baseline.json  短事件列
  demo.py           三幕端到端演示
tests/              17 个单元测试
```

## 快速上手

```bash
# 静态分析（不绑板卡也可，只算栈与响应）
python3 -m fwstack.cli analyze examples/programs/sampler.fw \
    examples/boards/boards.fwboard:NANO

# 交付可回放配置（二选一栈接法）；有缺口时退出码 2 且不写文件
python3 -m fwstack.cli deliver examples/programs/sampler.fw \
    examples/boards/boards.fwboard:NANO --stack-model shared --out build/cfg.json

# 校验指纹：程序或板卡源文件一旦改动（含只移动开放中断点），旧配置立即失效
python3 -m fwstack.cli check build/cfg.json

# 本地解释器回放短事件列，取回采样记录
python3 -m fwstack.cli replay build/cfg.json examples/events_baseline.json --mode SAMPLE

# 一键三幕演示
python3 examples/demo.py

# 测试
python3 -m unittest discover -s tests
```

## 三幕演示对应关系

1. **正常入口装入并回放**：基线固件在 NANO 上两种接法都达标，交付共享栈配置，
   提交短事件列，取回每个事件的到达/启动/完成/延迟/响应与实测栈峰值。
2. **只移动开放中断点**：`sampler_late.fw` 的函数帧与 `work` 全部不变，仅把开放
   区间后移。旧配置指纹失配、回放被拒；重算后栈峰值不变，但封闭区间 950→1440us，
   high 最坏响应 1640us 超过 1400us，该组合被标记响应违约并禁止交付。
3. **恢复程序、仅给新外设留内存**：同一固件放到 NANO_P，响应承诺不变；共享栈仍
   放得下，隔离栈因保留区碎片放不下，缺口精确到栈区与空闲块尺寸。NANO_F 进一步
   演示“空闲总量够却纯因碎片失败”。

## 描述文件文法（节选）

```
program 名称 {
    mode 名称 entry 入口 ;
    irq high|low handler 函数 arrivals N per_period ;
    func 名称(frame N bytes, work N us) [: mode 名称] {
        call 函数 ;
        irq on|off high|low|all ;
        region 名称 open high, low { ... }
    }
}

board 名称 {
    ram N bytes ;
    reserve 名称 [起, 止) ;
    period N us ;
    deadline high|low N us ;
}
```

中断处理函数不能被前台直接 `call`（只能由硬件进入），也不能限定模式；
保留区重叠或越界、模式专用函数不可达等都会在解析期报错。
