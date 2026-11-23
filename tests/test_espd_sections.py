import unittest
from unittest.mock import Mock
from espd import inspect_page, summarize


class SectionTests(unittest.TestCase):
    def test_contents_claim_needs_matching_body_and_printed_number(self):
        toc = inspect_page(Mock(extract_text=lambda: "3\nСодержание\n1. Назначение программы .... 4"),4)
        body = inspect_page(Mock(extract_text=lambda: "4\n1. Назначение программы\nОписание."),5)
        for pages,status in (([toc],"review"),([toc,body],"matched")):
            report = {"pages":pages}
            summarize(report)
            self.assertEqual(report["toc_checks"][0]["status"],status)
        body["printed_page_candidate"] = 7
        summarize(report)
        self.assertEqual(report["toc_checks"][0]["status"],"review")
    def test_list_of_required_sections_is_not_document_evidence(self):
        page = inspect_page(Mock(extract_text=lambda: "Следующие разделы:\n•\nсообщения оператору."), 1)
        self.assertEqual(page["headings"], [])

    def test_combined_wrapped_and_evidence(self):
        text = ("1. Назначение и условия выполнения программы\nТребуется терминал.\n"
                "2. Выполнение\nпрограммы\nПри запуске выводится сообщение об ошибке.")
        page = inspect_page(Mock(extract_text=lambda: text), 7)
        self.assertEqual(len(page["headings"]), 3)
        self.assertEqual(page["headings"][0]["method"], "combined_heading")
        self.assertEqual(len(page["message_evidence"]), 1)
        self.assertEqual(page["headings"][-1]["section"], "Выполнение программы")

    def test_only_body_headings_are_evidence(self):
        pages = [inspect_page(Mock(extract_text=lambda: text), number)
                 for number, text in enumerate(["Содержание\n1 Назначение программы",
                                                "2. НАЗНАЧЕНИЕ ПРОГРАММЫ\nСм. Сообщения оператору",
                                                ""], 1)]
        report = {"pages": pages}
        summarize(report)
        self.assertEqual(report["sections"]["Назначение программы"]["pages"], [2])
        self.assertEqual(report["sections"]["Сообщения оператору"]["status"], "review")
        self.assertFalse(pages[2]["has_text"])
