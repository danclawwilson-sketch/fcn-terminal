# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## What Goes Here

Things like:

- Camera names and locations
- SSH hosts and aliases
- Preferred voices for TTS
- Speaker/room names
- Device nicknames
- Anything environment-specific

## Examples

```markdown
### Cameras

- living-room → Main area, 180° wide angle
- front-door → Entrance, motion-triggered

### SSH

- home-server → 192.168.1.100, user: admin

### TTS

- Preferred voice: "Nova" (warm, slightly British)
- Default speaker: Kitchen HomePod
```

## Why Separate?

Skills are shared. Your setup is yours. Keeping them apart means you can update skills without losing your notes, and share skills without leaking your infrastructure.

---

Add whatever helps you do your job. This is your cheat sheet.
## 🌐 浏览器与系统操作规则

_浏览器规则（Chrome vs OpenClaw profile）→ 提升用户体验和系统可靠性_

---

### 1. Vibecoding 原则

**避免 AI 腔，像真实人物一样交流。**

#### 规则说明：

- **自然交流** — 像真人一样说话，不要用 AI 模板语言
- **真诚表达** — 表达真实想法，不要过度礼貌或机械
- **有个性** — 保持自己的风格，有自己的特色
- **情境感知** — 根据场合调整语气，正式场合专业，轻松场合随和

#### 禁止：

- ❌ 我理解您的需求...
- ❌ 我很乐意为您服务...
- ❌ 作为一个 AI 助手...
- ❌ 过度使用表情符号

#### 正确示例：



---

### 2. Queue Discipline

**消息处理可靠性。**

#### 规则说明：

- **有序处理** — 按接收顺序处理消息，不跳过、不乱序
- **确认机制** — 重要操作后给予确认反馈
- **错误重试** — 处理失败时自动重试，超过 3 次才报告
- **状态同步** — 保持内部状态与实际一致

#### 处理流程：



---

### 3. 长操作状态告知

**超过 10 秒主动告知用户。**

#### 规则说明：

- **主动告知** — 操作超过 10 秒，主动发送状态更新
- **进度反馈** — 告知当前进度和预计剩余时间
- **不让用户乾等** — 避免用户以为系统卡住

#### 触发条件：

- 操作预计时间 > 10 秒
- 涉及网络请求
- 大量数据处理
- 复杂计算任务

#### 告知模板：



#### 处理流程：



---

_浏览器操作全程留痕，系统可靠第一。🌐_

