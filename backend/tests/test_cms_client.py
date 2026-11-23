import httpx

from app.sources.cms_client import CmsClient


def test_lists_and_downloads_a_snap(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/cms/sprsnap/list":
            return httpx.Response(200, json=[{"filename": "b.tar"}, "a.tar", "../evil"])
        assert req.url.path == "/cms/sprsnap/download/b.tar"
        return httpx.Response(200, content=b"TAR")
    c = CmsClient("http://cms", transport=httpx.MockTransport(handler))
    assert c.list_snaps() == ["a.tar", "b.tar"]
    assert c.download("b.tar", tmp_path / "b.tar").read_bytes() == b"TAR"
