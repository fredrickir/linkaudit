# linkaudit

**返回 200 不算通过，指向那一页才算。**

`linkaudit` 校验 Markdown 文档里的外链：逐条给出可达性、最终落点，
并把「暂时不可达」和「已失效」严格分开。

- 零依赖，Python 3.10+ 标准库即可运行
- 单文件，`linkaudit.py` 拷走就能用
- 只发 HEAD 请求，不下载正文

<!-- ipos:install -->
## 安装

零依赖，Python 3.10+ 即可：

```bash
pip install fredrickir-linkaudit
```

装完直接用命令名调用：

```bash
linkaudit --help
```

也可以不用安装，直接把 `linkaudit.py` 拷走用——它只依赖标准库。

## 为什么需要它

维护文档的人大概都遇到过：某个链接某天开始跳到首页，或者直接超时。
读者不会说是链接坏了，只会说「文档不靠谱」。

`linkaudit` 的判断标准比「能打开」严一点：

| 状态 | 含义 | 处理建议 |
|---|---|---|
| **可用** | 直接返回 200 | 无 |
| **可用（经跳转）** | 跳到了另一个地址，且那一页是具体内容 | 无，但值得记一笔 |
| **指向不明确** | 跳到了站点首页，不是预期的那一页 | 换成直指具体页面的链接 |
| **已失效** | 404 / 410，服务器明确说不存在 | 找替代来源，或在链接旁注明原始出处 |
| **暂时不可达** | 超时、5xx、网络错误 | 稍后重试确认；**确认前不要判为死链** |
| **被拒绝访问** | 401 / 403，链接需要登录 | 考虑补一个公开版本 |

最后两类的区分是这个工具存在的理由：**把网络抖动算进死链，检查结果就会慢慢没人信。**

## 用法

```bash
python linkaudit.py --file README.md

# 放宽超时、重试更多次（网络差的时候）
python linkaudit.py --file README.md --timeout 20 --retries 2

# 机器可读输出
python linkaudit.py --file README.md --json
```

输出：

```
文件：README.md
链接总数：23
  可用：        18
  已失效：      2
  暂时不可达：  1
  指向不明确：  1
  被拒绝访问：  1

需要处理的链接（5 条）：
  行   42  [已失效]  https://example.com/old-doc
          HTTP 404：服务器明确表示该资源不存在
          建议：找替代来源，或在链接旁注明原始出处
  行   88  [指向不明确]  https://example.com/guide
          跳转到站点首页（https://example.com/），不是预期的那一页；链接本身可达但指向不明确
          建议：换成直指具体页面的链接，或确认站点是否改版
  行  103  [暂时不可达]  https://slow.example.com/page
          网络错误：timed out
          建议：稍后重试确认；确认前不要判为死链
```

退出码：有问题时 `1`，全部通过时 `0`，文件读不到时 `2`——
可以挂进 CI 当门禁。

## 链接是怎么提取的

两种写法都认：

- Markdown 链接：`[显示文本](https://example.com/page)`
- 裸链接：`https://example.com/page` 单独出现在一行里

跳过 `mailto:`、`tel:`、`javascript:`、`data:` 这类非 HTTP 协议，
以及以 `#` 开头的锚点。同一 URL 只查一次，保留第一次出现的行号。

代码块（``` 围栏）里的内容会被跳过——示例代码里的链接不需要检查。

## 已知局限

- **只发 HEAD 请求**。有些服务器对 HEAD 的响应和对 GET 不一样（尤其重定向处理），
  这种情况下可能出现误判。
- **不校验内容**。只确认「有个页面在那儿」，不检查页面内容是否还对得上。
  「指向那一页」指的是最终 URL 的路径，不是页面里的具体内容。
- **不检查锚点**。`https://example.com/page#section` 里的 `#section` 是否存在，它不管。
- **不判断「链接是否还值得保留」**。一个 200 但内容过时的页面，它会报「可用」。
- **可能被反爬机制拦住**。返回 403 的站点会被记为「被拒绝访问」，
  但其中一部分其实只是拒绝了 HEAD 请求。
- **不做并发**。逐条串行检查，链接多时慢——这是为了让输出稳定可读。
- **不检查 HTTPS 证书有效性**。证书过期的站点可能仍然返回 200。

## 跑测试

```bash
python test_linkaudit.py
```

测试用本地临时 HTTP 服务模拟各种响应（200 / 404 / 500 / 403 / 重定向），
不访问外网。

## 为什么还要做这个

现有的链接检查工具大多只回答「能不能打开」。
但对一个文档来说，**「能打开但跳到了首页」** 是个更隐蔽的问题——
它不报错，读者却要自己再找一遍。

`linkaudit` 多做一件事：看最终落点。同时，它把「没查到」和「查到了」分开处理，
不做那种「跑一遍脚本，输出一串红叉」的检查。

## 来源与参考

- Python `urllib.request` 文档：<https://docs.python.org/3/library/urllib.request.html>
- RFC 9110（HTTP 语义与状态码）：<https://www.rfc-editor.org/rfc/rfc9110>
- CommonMark 规范（链接语法）：<https://commonmark.org/>

---

## 关于作者

fredrickir 做外链落点校验。这个工具先把状态分清楚，再谈修不修。

- 个人主页：<https://fredrickir.github.io>
- 仓库：<https://github.com/fredrickir/linkaudit>

## 许可

MIT，见 [LICENSE](LICENSE)。
