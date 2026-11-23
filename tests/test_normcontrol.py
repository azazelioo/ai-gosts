import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf
from fastapi.testclient import TestClient

from app.normcontrol.api import api


class BackendTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = patch.dict(os.environ,NORMCONTROL_STORAGE=tmp.name)
        env.start()
        self.addCleanup(env.stop)
        self.client = TestClient(api,base_url="http://localhost")
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((40,40),"Example guide")
            self.data = pdf.tobytes()
        self.upload = {"document":("test.pdf",self.data,"application/pdf")}

    def test_catalog_persistence_and_source(self):
        fields = dict(number="EXAMPLE",edition="1",title="Example guide")
        response = self.client.post("/catalog/import",files=self.upload,data=fields)
        self.assertEqual(response.status_code,200)
        ident = response.json()["id"]
        self.assertEqual(self.client.get("/catalog/"+ident+"/source").content,self.data)
        self.client.post("/catalog/import",files=self.upload,data=fields).raise_for_status()
        hits = self.client.get("/catalog/search",params={"q":"Example guide"}).json()["hits"]
        self.assertEqual([(h["id"],h["page"]) for h in hits],[(ident,1)])
        self.assertEqual(self.client.post("/analyze",files={"document":("bad.pdf",b"broken")}).status_code,422)

    def test_uncovered_standard_cache_and_offline_recovery(self):
        fields = dict(number="EXAMPLE",edition="1",title="Example guide")
        ident = self.client.post("/catalog/import",files=self.upload,data=fields).json()["id"]
        with patch("app.normcontrol.service.Dispatcher") as factory:
            model = factory.return_value
            model.model = "local-test"
            model.identity.side_effect = ValueError("offline")
            partial = self.client.post("/analyze",files=self.upload).json()
            self.assertEqual(partial["status"],"partial")
            model.identity.side_effect = None
            model.identity.return_value = "weights-v1"
            model.choose.return_value = {"1":"s0"}
            first = self.client.post("/analyze",files=self.upload).json()
            self.assertEqual(first["pages"][0]["unsupported_standard"]["id"],ident)
            self.assertEqual(first["pages"][0]["status"],"review")
            self.assertTrue(first["profile_cache_hit"])
            again = self.client.post("/analyze",files=self.upload).json()
            self.assertTrue(again["cache_hit"])
            self.assertEqual(again["id"],first["id"])
            self.assertEqual(self.client.get(first["report_url"]).status_code,200)

    def test_local_browser_boundary(self):
        self.assertEqual(self.client.get("/catalog",headers={"Host":"example.org"}).status_code,403)
        self.assertEqual(self.client.post("/analyze",files=self.upload,
                         headers={"Origin":"https://example.org"}).status_code,403)
        response = self.client.get("/",headers={"Origin":"http://localhost"})
        self.assertEqual(response.status_code,200)
        self.assertIn("frame-ancestors 'none'",response.headers["content-security-policy"])

    def test_resource_limits(self):
        for variable in ("NORMCONTROL_MAX_REQUEST_BYTES","NORMCONTROL_MAX_RENDER_PIXELS"):
            with patch.dict(os.environ,{variable:"32"}):
                self.assertEqual(self.client.post("/analyze",files=self.upload).status_code,413)

    def test_empty_page_skips_generation(self):
        with pymupdf.open() as pdf:
            pdf.new_page()
            data = pdf.tobytes()
        with patch("app.normcontrol.service.Dispatcher") as factory:
            model = factory.return_value
            model.model = "local-test"
            model.identity.return_value = "weights"
            result = self.client.post("/analyze",files={"document":("blank.pdf",data)}).json()
            self.assertFalse(result["pages"][0]["ai_evaluated"])
            self.assertEqual(result["pages"][0]["modules"],[])
            model.choose.assert_not_called()

    def test_model_suggestion_alone_cannot_activate_a_handler(self):
        self.client.post("/catalog/import",files=self.upload,data=dict(
            number="EXAMPLE",edition="1",title="Example guide")).raise_for_status()
        with patch("app.normcontrol.service.Dispatcher") as factory:
            model = factory.return_value
            model.model = "local-test"
            model.identity.return_value = "weights"
            model.choose.return_value = {"1":"eskd"}
            page = self.client.post("/analyze",files=self.upload).json()["pages"][0]
            self.assertTrue(page["routing_conflict"])
            self.assertEqual(page["modules"],[])
            self.assertEqual(page["status"],"review")
