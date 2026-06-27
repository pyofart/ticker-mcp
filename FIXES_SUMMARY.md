# ticker-mcp 修复指南与思路汇总

> 本文档针对 `pyofart/ticker-mcp` 仓库的早期评估结果，梳理了项目中存在的安全、稳定性及代码质量问题，并给出了具体的修复思路。

## 一、问题与修复清单

| 序号 | 维度 | 待修复问题 | 风险等级 | 修复思路 |
| :--- | :--- | :--- | :--- | :--- |
| 1 | 规范 | 命名不一致（仓库名为 `ticker-mcp`，包名与Server名为 `toolkit-mcp`） | 低 | 全局统一命名为 `ticker-mcp` |
| 2 | 稳定性 | `_FileStore._save` 写入失败时 `pass` 静默吞错 | 高 | 捕获异常后记录日志并抛出 `RuntimeError` |
| 3 | 并发 | `_FileStore` 无文件锁，多并发写入会导致数据覆盖 | 中 | 引入 `threading.Lock` 及原子化临时文件替换 |
| 4 | 安全 | `a2a_register` 缺乏输入校验，可注入任意非法字段 | 中 | 增加类型检查与非空校验，校验失败抛出 `ValueError` |
| 5 | 安全 | SSE 模式默认绑定 `0.0.0.0` 且无任何鉴权机制 | 极高 | 默认绑定至 `127.0.0.1`，增加 Token 鉴权中间件 |
| 6 | 安全 | hash 任务中使用了已被认为不安全的 MD5 算法 | 低 | 移除 MD5，仅保留 SHA256 |

## 二、详细修复思路解析

### 1. 命名规范与配置统一

**问题**：项目仓库名为 `ticker-mcp`，但在 `pyproject.toml` 的 `name` 字段、README 标题及 Server 注册名中却使用了 `toolkit-mcp`，容易造成使用和检索混乱。

**修复思路**：
- 修改 `pyproject.toml` 中的 `name = "toolkit-mcp"` 为 `name = "ticker-mcp"`。
- 同步更新 README 中的标题与描述，保持全仓库命名一致。

### 2. 数据持久化安全与并发控制

**问题**：`_FileStore` 在写入 JSON 文件时，如果发生 `IOError` 会直接 `except: pass`，导致 A2A 注册数据可能丢失而用户无感知。同时，缺乏并发控制，多个请求同时写入会导致后写覆盖先写。

**修复思路**：
- **错误暴露**：不静默吞错，改为使用 `logging` 记录错误，并向上抛出 `RuntimeError("Data persistence failed")`，让调用方明确感知失败。
- **并发锁**：在 `_FileStore` 初始化时创建 `threading.Lock()`，在 `_load` 和 `_save` 操作时加锁，保证线程安全。
- **原子写入**：采用"临时文件 + `os.replace()`"策略。先写入 `.tmp` 临时文件，写入成功后再重命名替换原文件，避免写入一半崩溃导致原数据文件损坏。

### 3. A2A 接口输入校验

**问题**：`a2a_register` 接受 `agent_name`、`capabilities`、`contact_endpoint` 等参数，但未做任何类型或非空校验，任意空值或错误类型会直接落盘，可能破坏数据结构完整性。

**修复思路**：
- 在方法入口处增加防御性编程逻辑：

```python
if not agent_name or not isinstance(agent_name, str):
    raise ValueError("agent_name must be a non-empty string")
if not capabilities or not isinstance(capabilities, list):
    raise ValueError("capabilities must be a non-empty list of strings")
```

### 4. SSE 传输安全加固

**问题**：SSE 模式通过 argparse 启动后，默认监听 `0.0.0.0:8080`，且没有任何鉴权。这意味着在同一局域网或公网环境下，任何人都可以随意调用工具、注册 Agent 或提交任务。

**修复思路**：
- **限制默认监听**：将 `--host` 的默认值从 `0.0.0.0` 修改为 `127.0.0.1`，仅允许本机访问，除非用户显式指定公网绑定。
- **增加鉴权中间件**：在 Starlette 应用中添加 `BaseHTTPMiddleware`。支持通过环境变量或命令行参数传入 `--token`。中间件会校验请求头 `Authorization: Bearer <token>` 或 URL 查询参数中的 `token`，不匹配则直接返回 `401 Unauthorized`。
- **安全警告**：当用户显式绑定 `0.0.0.0` 但未配置 Token 时，在终端打印明显的安全警告日志。

### 5. 移除不安全哈希算法

**问题**：在 `_auto_process_task` 的 hash 任务中，同时使用了 MD5 和 SHA256 对数据生成摘要。MD5 已被证明存在碰撞风险，不适合在现代应用中使用（即使是作为非加密用途的摘要）。

**修复思路**：
- 直接删除生成 MD5 哈希的代码逻辑，仅返回 sha256 哈希值，减少不必要的攻击面和技术债。
