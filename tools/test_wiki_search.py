"""回归正文摘要与无脚本目录，运行：python -m unittest tools.test_wiki_search。"""
import unittest

from wiki.build_wiki import md_to_html, page_toc, search_summary


class WikiSearchTests(unittest.TestCase):
    def test_summary_uses_readable_body_not_metadata_or_code(self):
        body = "# 标题\n> 数据来源：示例\n## 故事\n```\n秘密代码\n```\n这是一段[[zh_cn/items/星核|星核]]记录。"
        self.assertEqual(search_summary(body), "这是一段星核记录。")

    def test_summary_is_bounded(self):
        self.assertEqual(len(search_summary("星" * 500)), 240)

    def test_toc_uses_rendered_heading_ids_and_escapes_labels(self):
        rendered = md_to_html("## 第一章\n正文\n## 第一章\n正文\n## **角色** & 光锥", 1)
        toc = page_toc(rendered)
        self.assertIn('href="#第一章"', toc)
        self.assertIn('href="#第一章-2"', toc)
        self.assertNotIn("<strong>", toc)
        self.assertIn("角色 &amp; 光锥", toc)

    def test_toc_omitted_for_short_page(self):
        self.assertEqual(page_toc(md_to_html("## 唯一章节\n正文", 1)), "")


if __name__ == "__main__":
    unittest.main()
