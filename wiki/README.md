# HSR 静态 Wiki

零依赖静态站，`file://` 可直接打开 `index.html`。

## 本地预览

直接双击 `wiki/index.html`，或：

```powershell
# 重新生成
python wiki/build_wiki.py
```

## 目录

- `index.html` 首页（搜索 + 导航）
- `assets/` 样式与脚本
- `build_wiki.py` 生成器（扫描 `zh_cn/` 全量 md）
- `pages/`（生成物，已 gitignore）
- `data/titles.js`、`data/link_report.js`（生成物，已 gitignore）

## GitHub Pages

- 站点：https://ganmayou2333.github.io/hsr-knowledge-base/
- `.github/workflows/pages.yml` 在 push 到 `main` 时先运行质量检查，通过后浅克隆 SRR → copy_icons.py → build_wiki.py 并发布。PR 也运行相同的质量检查；是否阻止合并取决于仓库的保护规则。
- 首次需在仓库 Settings → Pages 选 GitHub Actions 来源。
- 图标为构建时从 StarRailRes 拉取，**不随仓库分发**。

## 搜索与检查

- 搜索索引由构建脚本生成，包含标题、路径和每篇正文开头最多 240 字的可读摘要；摘要之外的正文不会命中。无需网络，支持 `file://`；禁用 JavaScript 时仍可浏览分类和正文。
- 2026-11-20 本地完整数据集生成的 `titles.js` 为 2,096,808 字节；文件大小随收录内容变化，干净检出可能不同。
- 以下命令在仓库根目录执行；`check_quality.py` 对已记录的数据区失效链接使用精确基线，新增失效链接或字段异常会失败。修复基线中的链接后，应同时更新基线。

```powershell
python wiki/build_wiki.py
python tools/check_quality.py
python tools/verify_site.py
python tools/verify_docs.py
python -m unittest tools.test_wiki_search tools.test_quality_gate tools.test_preview_review tools.test_preview_artifact tools.test_package_preview -v
node --check wiki/assets/app.js
node --check wiki/assets/home.js
$env:WIKI_BROWSER = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
node tools/test_wiki_browser.mjs
```

浏览器检查需要 Node.js 22 或更新版本与 Chrome/Edge，路径按本机安装位置调整。它使用 `file://` 检查 1280×800、600×900 和 390×900 视口、正文摘要检索、键盘打开、减少动态效果及禁用 JavaScript 后的浏览。

---

## 素材范围与署名

- 本站**仅发布 `icon/**` 图标**（头像 / 立绘 / 光锥 / 遗器 / 物品 / 奇物），**不发布**游戏字体与 `image/**` 高清大图。
- 图标在构建时从 [StarRailRes](https://github.com/Mar-7th/StarRailRes)（AGPL-3.0）拉取，**不随仓库分发**。
- 页脚须保留：`© 米哈游版权所有 · 非官方非商业同人整理 · AGPL-3.0（仅覆盖代码与编排）· 图标来自 StarRailRes`。
