# 华为负一屏（HiBoard）任务推送 API — 契约规范

> **来源**：从 ClawHub 技能包 `ganhaiyang3/today-task` v1.0.17 反向提取，
> 依据为**实际代码行为**而非其文档（两者有多处冲突，见 §9）。
>
> **验证**：2026-07-26 对生产端点发起约 100 次真实请求（约 50 次成功推送），
> 配合真机负一屏截图逐项核对。**未标注「未验证」的条目均为实测结论。**

---

## 1. 速览

| 项 | 值 |
|---|---|
| 端点 | `POST .../distribution/message/cloud/claw/msg/upload` |
| 认证 | body 字段 `authCode`（无 header 认证） |
| 必需 header | `Content-Type`、`x-trace-id`（非空即可） |
| body 外层 | **必须包一层 `{"data": ...}`** |
| 正文上限 | **30,720 个 UTF-16 码元** |
| 成功判定 | `code == "0000000000"` |
| 卡片身份 | `scheduleTaskId`，同值原地更新 |
| 卡片标题 | **`summary`**（不是 `scheduleTaskName`） |
| 推送通知 | **不发送**，静默进入负一屏 |
| 删除卡片 | **不支持**，无此接口 |

四个最容易踩的点：

1. 漏掉 `{"data": ...}` 外层包装
2. 漏掉 `x-trace-id` header
3. 用 Python `len()` 校验正文长度（对 emoji 会少算一半）
4. 以为卡片标题是 `scheduleTaskName`（实际是 `summary`）

## 2. 端点与认证

```
POST https://hiboard-claw-drcn.ai.dbankcloud.cn/distribution/message/cloud/claw/msg/upload
```

服务方：华为云，节点 DRCN（东莞）。

**认证**：无 header 认证，授权码作为 body 字段 `authCode` 传输。实测为 12 位字母数字。

获取路径（需华为手机）：
负一屏 → 左上角头像 → 我的 → 右上角设置 → 动态管理 → 关联账号 → Claw 智能体

> 包内另有历史端点 `.../openclaw/upload`，仅存在于测试代码中。
> v1.0.16 changelog 称"切换使用更安全的请求域名"，视为**已废弃**。

**不存在的接口**：`.../msg/delete`、`.../msg/query`、`.../msg/list` 均返回 HTTP 404。
推送是唯一可用操作 —— 这带来重要后果，见 §6.3。

## 3. 请求

### 3.1 Headers

```
Content-Type: application/json; charset=utf-8
x-trace-id:   <非空字符串>
User-Agent:   OpenClaw-TaskPusher/2.0
```

`x-trace-id` **必需**，缺失或空串返回 `0000500001`。
但服务端**只校验非空、不校验格式** —— 单字符 `"x"` 即通过。
`User-Agent` 未见校验。

### 3.2 Body

⚠️ **注意最外层的 `data` 包装。**

```json
{
  "data": {
    "authCode": "<授权码>",
    "msgContent": [
      {
        "msgId":            "<string>",
        "scheduleTaskId":   "<string>",
        "scheduleTaskName": "<string>",
        "summary":          "<string>",
        "result":           "<string>",
        "content":          "<markdown string>",
        "source":           "<string>",
        "taskFinishTime":   1769424000
      }
    ]
  }
}
```

### 3.3 字段表

全部字段均为必填，缺任一项会被校验拒绝。

| 字段 | 类型 | 说明 |
|---|---|---|
| `authCode` | string | 授权码 |
| `msgContent` | array | 非空数组。**支持多元素**，一次请求可创建/更新 N 张卡片 |
| ─ `msgId` | string | 单条消息 ID。**原技能文档遗漏了此字段**，但服务端必填 |
| ─ `scheduleTaskId` | string | **卡片身份键**，同值原地更新。见 §6.1 |
| ─ `scheduleTaskName` | string | 必填，但**未见于任何已观察视图**。用途不明 |
| ─ `summary` | string | **列表态卡片标题**。见 §6.2 |
| ─ `result` | string | **展开态副标题** |
| ─ `content` | string | 正文 Markdown。长度限制见 §4，可用语法见 §7 |
| ─ `source` | string | **展开态主标题**兼列表态「任务来源」。见 §6.2 |
| ─ `taskFinishTime` | number | **UTC 秒级**时间戳（非毫秒）。渲染时按本地时区换算 |

### 3.4 各字段该填什么

字段名与实际显示位置严重不对应（见 §6.2），因此单独给出填写建议：

| 字段 | 建议值 | 反例 |
|---|---|---|
| `summary` | 带信息量的一句话：`今日早报已生成 · 3 条重点` | `新闻汇总`（浪费了最显眼的位置） |
| `source` | 有辨识度的服务名：`每日简报` | `OpenClaw`（所有卡片长得一样） |
| `result` | 执行状态：`任务已完成` / `任务异常中断` | 与 `summary` 重复的内容 |
| `scheduleTaskName` | 内部任务名即可，反正不显示 | — |
| `scheduleTaskId` | 周期性任务固定值：`daily_brief` | 每次随机（见 §6.3） |

### 3.5 ID 生成

```python
# 周期性任务：固定 scheduleTaskId，只让 msgId 变化
schedule_task_id = "daily_brief"
msg_id           = f"{schedule_task_id}_{int(time.time())}"
```

一次性任务可用随机 ID，但**每个新 ID 都会永久新增一张删不掉的卡片**，见 §6.3。

若要从任务名生成 slug，注意 **Python 中中文字符的 `isalnum()` 返回 `True`**。
原实现用 `c.isalnum()` 过滤，结果生成出 `spec_验证测试_20260726_145026_9aa6ef40`
这类非 ASCII ID。服务端接受，但作为幂等键建议限制为 ASCII —— 实现见 §8 的 `slugify()`。

## 4. 正文长度限制

**上限 = 30,720（30 × 1024）个 UTF-16 码元**，超出返回 `0000500002`。

二分实测边界：

| 填充内容 | 通过 | 拒绝 |
|---|---|---|
| ASCII `a` | 30,720 字符 | 30,721 |
| 中文 `长` | 30,720 字符（= 92,160 字节） | 30,721 |
| emoji `🚀` | **15,360 个**（= 30,720 码元） | 15,361 |

三者上限一致于 30,720 **码元**，据此可以确定：

- 按**字符**计而非字节 —— 中文与 ASCII 的字符上限相同
- 计的是 **UTF-16 码元**（Java `String.length()` 语义）—— emoji 一个占 2 个额度

⚠️ **Python 的 `len()` 数的是码点，对非 BMP 字符会少算一半。** 正确校验：

```python
def utf16_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2

assert utf16_len(content) <= 30720
```

> 原技能包 `config.json` 的 `max_content_length: 5000` 既**从未被代码执行**，
> 数值也比真实上限保守 6 倍。

## 5. 响应与错误码

### 5.1 成功

```json
{ "code": "0000000000", "desc": "OK" }
```

**只用 `code` 判定，不要用 `desc`** —— 实测返回 `"OK"`，而原技能文档写的是 `"成功"`。

实测观察到的成功码只有 `0000000000`。原实现还接受
`{"0", 0, "200", 200, "success", "SUCCESS"}` 一组值，属推测性兜底，
未观察到服务端实际使用。

### 5.2 错误码

| code | `desc` | 含义 | 处理 |
|---|---|---|---|
| `0000900034` | `The authCode is invalid` | 授权码无效 / 未关联账号 | 引导用户重新获取授权码 |
| `0000500001` | `Parameter x-trace-id is empty` | 缺少或空的 `x-trace-id` | 补上任意非空字符串 |
| `0000500002` | `Parameter content size is too long` | 正文超过 30,720 码元 | 截断正文，见 §4 |
| `0000400001` | `The count reached the upper limit` | **推送次数配额达上限** | 停止重试，等待配额重置 |
| `0200100004` | `Receive error code N from CP` | 云推送服务异常 | 需二次解析，见 §5.3 |

> `0000400001` 于 2026-07-26 17:15 实测触发：短时间内数百次推送（事故性递归）后出现，
> 此前正常使用约 60 次未触发。配额的准确阈值与重置周期未知（疑似按日）；
> spec 初测时 15 次无间隔请求（2.5 req/s）未触发，说明限的是**总量**而非瞬时速率。
>
> **配额按账号计，不按授权码**（✅ 实测）：触发限额后在负一屏重新生成授权码，
> 新码推送仍返回 `0000400001`。换码无法绕过配额。
>
> **重置时间 = 午夜**（✅ 实测，2026-07-26/27）：23:58:29 仍返回 `0000400001`，
> 次日 00:10 后首次尝试即成功——配额按自然日计，午夜刷新。

失败时消息可能在 `desc` 或 `message`，两者都要读。

> ⚠️ `0000500001` 与 `0000500002` **在原技能包的错误码映射表里都不存在**
> （`hiboards_client.py:195-216` 只映射了另外两个）。二者都会落进"未知错误码"
> 兜底分支，向用户输出「检查网络连接是否正常 / 确认授权码是否正确」——
> 与真实原因毫无关系。其中 `0000500002` 尤其容易触发：**一篇长日报就能踩到。**

### 5.3 CP 二级错误码

`0200100004` 的 `desc` 形如 `Receive error code <N> from CP`：

```python
m = re.search(r"Receive error code (\d+) from CP", desc)
```

| CP code | 含义 | 处理 |
|---|---|---|
| `82600017` | 设备未联网，或未登录华为账号 | 检查网络 + 登录华为账号 |
| `82600013` | 负一屏「服务动态」推送开关已关闭 | 负一屏 → 我的 → 动态管理 → AI 任务完成通知 → 开启场景开关与服务提供方开关 |
| `82600005` | 服务动态云服务异常 | 稍后重试 |

> 三个 CP 码取自原实现的映射表，**未实机触发验证**。

## 6. 卡片模型

### 6.1 身份与幂等

**`scheduleTaskId` 是一张卡片的唯一身份。**

| 行为 | 结果 |
|---|---|
| 同 `scheduleTaskId` 重复推送 | **原地更新同一张卡片**，不新增 |
| 不同 `scheduleTaskId` | 各自独立卡片 |
| `msgContent` 传 N 元素 | 一次请求创建/更新 N 张卡片 |

**实测证据**：约 50 次成功推送共使用 7 个不同 `scheduleTaskId`，
负一屏「历史记录」中**恰好 7 张卡片**：

| `scheduleTaskId` | 成功推送 | 卡片 |
|---|:--:|:--:|
| `spec_probe_len` | ~27 | 1 |
| `spec_probe_rate` | 15 | 1 |
| `spec_probe_multi_1` / `_2` / `_3` | 各 2 | 3 |
| `spec_probe_notify` | 1 | 1 |
| 首次测试（随机 uuid） | 1 | 1 |

**卡片数 = 不同 ID 数，与推送次数无关。**

补充语义：

- **除 `scheduleTaskId` 外所有字段都随更新变化** —— 标题、来源、正文均跟随
  最新一次成功推送
- **被拒请求不影响已有卡片** —— 内容停留在最后一次成功推送，
  超长等错误不会把卡片刷空
- **被拒请求不产生卡片** —— 无效授权码那次未留任何痕迹

### 6.2 字段与显示的对应

⚠️ **本节是本规范最反直觉的部分。** 字段命名与实际显示位置严重不对应，
且原技能包给 `summary` / `scheduleTaskName` 赋了相同的值，
使得从外部观察根本无法区分二者。以下经**「五字段各带唯一标签」的对照实验**确认。

**列表态**（负一屏 / 历史记录）：

| 显示位置 | 字段 |
|---|---|
| 卡片标题 | **`summary`** |
| 「任务时间」 | `taskFinishTime`（本地时区） |
| 「任务来源」 | `source` |
| 状态标签 | 系统赋予，实测恒为「持续提醒中」 |

**展开态**（点开后的详情页）：

| 显示位置 | 字段 |
|---|---|
| 顶部主标题 | **`source`** |
| 顶部副标题 | **`result`** |
| 正文区 | `content` |
| 底部页脚 | **系统自动追加** `YYYY年MM月DD日 HH:mm 内容由AI生成` |

四点需要特别注意：

1. **`summary` 才是列表态卡片标题** —— 用户在负一屏上唯一能看到的文本就是它。
2. **`scheduleTaskName` 未见于任何视图** —— 列表态、展开态均不显示，
   且系统不发通知（见下），目前找不到它的去处。
3. **`source` 不是纯元数据** —— 它在展开态是**主标题**、列表态是「任务来源」。
4. **页脚由系统追加** —— 无需（也不应）在 `content` 里自行写时间戳或 AI 声明。

**不发送推送通知**：实测约 50 次推送、7 个不同 `scheduleTaskId`，
**未产生任何系统通知**，卡片静默进入负一屏。
因此「持续提醒中」指的是卡片在负一屏的**驻留状态**，而非会主动提醒用户。

### 6.3 卡片只增不减

- **无删除接口**（`.../msg/{delete,query,list}` 均 404）
- 同 ID 只能**覆盖内容**，无法移除卡片本身
- 卡片持续堆积在「历史记录」中，只能由用户**手动从 UI 清除**

因此 `scheduleTaskId` 的命名是**不可逆决策**：

| 场景 | 策略 | 后果 |
|---|---|---|
| 周期性任务（每日早报） | 固定 ID | 永远只占 1 张卡 |
| 一次性任务 | 每次新 ID | **每次永久新增一张卡** |

> 原实现默认每次拼 uuid 生成新 ID，与它自己文档里"周期性任务此 ID 需保持一致"
> 的说法相反。**用它做每日早报，一年后会堆积 365 张删不掉的卡片。**

## 7. Markdown 支持度

真机截图逐项核对：

| 特性 | 支持 | 备注 |
|---|:--:|---|
| `#` `##` `###` 标题 | ✅ | 三级字号区分明显 |
| `**粗体**` | ✅ | |
| `*斜体*` | ✅ | |
| `~~删除线~~` | ✅ | |
| `` `行内代码` `` | ✅ | 等宽字体 |
| `---` 分割线 | ✅ | |
| 无序列表 / 嵌套 | ✅ | 嵌套有缩进 |
| 有序列表 | ✅ | |
| `>` 引用 | ✅ | 左侧竖线 |
| 表格 | ✅ | 带完整边框 |
| 围栏代码块 | ✅ | **带语法高亮**（Python 已验证） |
| `[文字](url)` 链接 | ✅ | 蓝色可点 |
| `![](url)` 外链图片 | ✅ | 能加载，但**按原始尺寸显示、无缩放控制** |
| 任务列表 `- [x]` | ❌ | 原样输出 `[x]` / `[]` 字面量 |
| 表格对齐 `:--:` `---:` | ❌ | 一律按左对齐渲染 |
| 换行 | ✅ | **单个 `\n` 即换行**（不遵循 CommonMark 软换行合并），行尾双空格硬换行同样有效，两者渲染无差别（2026-07-27 真机 A/B 对比实测） |

**结论**：表格与代码块均可用，日报/报告类内容无需降级为纯文本排版。
避免任务列表与表格对齐语法。

正文中的反斜杠等转义字符**服务端与渲染端均正常处理**，客户端不应做任何预处理
（原实现在这里制造了数据损坏，见 §9 第 1 条）。

## 8. 参考实现

零依赖（仅标准库），已修正 §9 列出的全部缺陷。**本实现已实测跑通。**

```python
import json, re, time, uuid
import urllib.request, urllib.error

ENDPOINT = ("https://hiboard-claw-drcn.ai.dbankcloud.cn"
            "/distribution/message/cloud/claw/msg/upload")
MAX_UTF16 = 30720

ERR_HINTS = {
    "0000900034": "授权码无效或未关联，请到负一屏重新获取",
    "0000500001": "缺少 x-trace-id header",
    "0000500002": f"正文超过 {MAX_UTF16} 个 UTF-16 码元",
}
CP_HINTS = {
    "82600017": "设备未联网或未登录华为账号",
    "82600013": "负一屏「服务动态」推送开关已关闭",
    "82600005": "服务动态云服务异常，请稍后重试",
}


def utf16_len(s: str) -> int:
    """按服务端语义计长：UTF-16 码元数，emoji 计 2。"""
    return len(s.encode("utf-16-le")) // 2


def slugify(name: str) -> str:
    """任务名 → ASCII slug。非 ASCII 字符被剔除，
    故纯中文名会退化为空串，此时回退到 'task'。"""
    s = "".join(c if c.isascii() and c.isalnum() else "_" for c in name).lower()
    return re.sub(r"_+", "_", s).strip("_") or "task"


def push(auth_code, task_name, content, *, summary=None, result="任务已完成",
         source="OpenClaw", schedule_task_id=None, timeout=30):
    """推送任务结果到负一屏。返回 (ok: bool, detail: dict | str)。

    schedule_task_id  卡片身份。周期性任务务必固定传入同一值，否则每次
                      推送都会永久新增一张无法删除的卡片。
    summary           列表态卡片标题 —— 用户唯一能看到的文本。省略则用
                      task_name，但强烈建议写成带信息量的一句话。
    source            展开态主标题。建议填有意义的服务名而非 "OpenClaw"。
    task_name         写入 scheduleTaskName，实测不显示于任何视图。
    """
    if utf16_len(content) > MAX_UTF16:
        return False, f"正文过长：{utf16_len(content)} > {MAX_UTF16} 码元"

    if schedule_task_id is None:
        schedule_task_id = f"{slugify(task_name)}_{uuid.uuid4().hex[:12]}"

    now = int(time.time())
    body = {"data": {
        "authCode": auth_code,
        "msgContent": [{
            "msgId":            f"{schedule_task_id}_{now}",
            "scheduleTaskId":   schedule_task_id,
            "scheduleTaskName": task_name,
            "summary":          summary or task_name,
            "result":           result,
            "content":          content,   # 原样发送，不做任何反转义
            "source":           source,
            "taskFinishTime":   now,       # UTC 秒
        }],
    }}

    req = urllib.request.Request(
        ENDPOINT, method="POST",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent":   "OpenClaw-TaskPusher/2.0",
            "x-trace-id":   f"push-{now}-{uuid.uuid4().hex[:8]}",  # 必需，非空即可
        })

    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"

    code = str(data.get("code", ""))
    if code == "0000000000":
        return True, data

    desc = data.get("desc") or data.get("message") or ""
    hint = ERR_HINTS.get(code, "")
    if code == "0200100004":
        m = re.search(r"Receive error code (\d+) from CP", desc)
        if m:
            hint = CP_HINTS.get(m.group(1), f"未知 CP 错误码 {m.group(1)}")
    return False, f"{code} {desc}" + (f" — {hint}" if hint else "")
```

用法：

```python
ok, detail = push(
    auth_code="<你的授权码>",
    task_name="daily_brief",                    # 内部名，不显示
    summary="今日早报已生成 · 3 条重点",          # ← 卡片标题
    source="每日简报",                           # ← 展开态主标题
    result="任务已完成",
    content=markdown_text,
    schedule_task_id="daily_brief",             # 固定值 → 永远只占 1 张卡
)
if not ok:
    print("推送失败:", detail)
```

对比原技能包约 90KB Python，功能等价且不含 §9 的任何缺陷。

## 9. 原技能包的已知缺陷

### 9.1 缺陷清单

若你打算直接使用 `ganhaiyang3/today-task` 而非自建：

| # | 问题 | 影响 |
|:--:|---|---|
| 1 | `_preprocess_content()` 无条件反转义 `\\t` `\\r` `\\\\` `\"` `\'` | **正文数据损坏**：`C:\temp` → `C:<TAB>emp`。服务端与渲染端本身正常，是客户端自造 |
| 2 | `summary` 与 `scheduleTaskName` 被赋予相同的值 | 卡片标题退化，且掩盖了真实映射关系。详见 §9.2 |
| 3 | 错误码表缺 `0000500001` / `0000500002` | 正文超长时提示"检查网络连接"，与真实原因无关 |
| 4 | 默认每次生成新 `scheduleTaskId` | 周期性任务天天堆卡片，且删不掉 |
| 5 | `source` 硬编码 `"OpenClaw"` | 所有卡片展开后主标题相同 |
| 6 | `config.py:84-89` 无条件覆写 `auth_code` | 本地 `config.json` 配授权码**完全无效**，与文档所述的两级优先级不符 |
| 7 | `update_checker.py:132` 硬编码 `powershell` | macOS/Linux 上更新检查必然失败，每次推送触发假告警 |
| 8 | `save_records` / `records_dir` / `max_records` / `max_content_length` / `log_level` | 五个配置项定义了但**无任何代码读取**，`push_records/` 无限增长 |
| 9 | `task_push.py:29-31` fallback 类定义在错误的 except 块 | 核心模块导入失败会静默吞掉，随后 NameError 崩溃 |
| 10 | `hiboards_client.py:188` 响应无状态字段时默认判成功 | 建议反过来：拿不到 `code` 即视为失败 |
| 11 | `SimpleHiboardsClient` 是死代码，且**不加 `{"data": ...}` 包装** | 照它抄会失败 |

### 9.2 专述：`summary` —— 实现违背了自身文档

原包 `SKILL.md:307-309` 对两个字段的定位其实**是准确的**：

```
"scheduleTaskName": "string", // 任务名称，必填，如"生成日报任务、生成新闻任务"
"summary":          "string", // 任务摘要，必填，说明具体是什么任务，
                              //   以及任务的执行状态，比如"生成新闻早报任务已完成"
```

文档明确把 `summary` 定位为**更具描述性、带执行状态**的字段 ——
与实测结论"`summary` 是列表态卡片标题"完全吻合。

但实现（`task_pusher.py:117-118`）把两者赋成了同一个值：

```python
"scheduleTaskName": task_name,       # 任务名称
"summary": task_name,                # 任务摘要   ← 同一个 task_name
```

后果有三层：

1. 卡片标题退化成干巴巴的任务名，丢掉了文档要求的执行状态信息
2. 两字段值相同，**从外部观察无法区分谁才是标题** —— 本规范初版即因此误判
3. 基于该包二次开发的人不会意识到应该优化 `summary`

> 两份文档均**未说明任何字段的显示位置**（哪个是标题、哪个进正文），
> 这大概率就是作者把两者填成一样的原因。

### 9.3 安全性结论

全包无 `eval` / `exec` / `pickle` / 混淆，唯一 `subprocess` 调用是硬编码常量、
无注入面，仅两个网络目的地，TLS 校验未关闭，授权码在日志中脱敏。

**不是恶意软件**，是文档篇幅远超代码、且文档大量失真的 vibe coding 产物。

## 10. 未验证项

- **限流阈值** —— 刻意未测。15 次无间隔请求（2.5 req/s）全部成功，
  常规用法无风险；进一步压测生产端点有账号风控风险，故止步于此
- **卡片是否自动过期** —— 实测状态标签恒为「持续提醒中」，未观察到消失
- **CP 二级错误码**（`826000xx`）—— 取自原实现映射表，未实机触发
- **`msgContent` 单次元素数上限** —— 已验证 3 个可用，未探边界
- **`scheduleTaskName` 的用途** —— 必填，但列表态、展开态均不显示，
  且系统不发通知。可能仅用于服务端侧记录
