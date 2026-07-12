from starlette.testclient import TestClient

from forge.api.app import create_app
from forge.llm.fake import FakeLLM
from forge.store.config import ForgeConfig, ModelConfig


def make_client(tmp_path, script=None):
    cfg = ForgeConfig(models=[ModelConfig(id="m", display_name="m")],
                      default_model="m", max_concurrent=3)
    app = create_app(home=tmp_path / "home", config=cfg, llm=FakeLLM(script or []))
    return TestClient(app)


def workspace(tmp_path):
    # Use a dedicated subdir as the session cwd so the app's home dir (created
    # under tmp_path) never leaks into fs listings.
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return ws


def new_session(client, cwd):
    return client.post("/api/sessions", json={"cwd": str(cwd)}).json()["id"]


def test_list_root_and_subdir(tmp_path):
    ws = workspace(tmp_path)
    (ws / "b_dir").mkdir()
    (ws / "A_dir").mkdir()
    (ws / "z.txt").write_text("hi")
    (ws / "a.txt").write_text("yo")
    (ws / "b_dir" / "nested.txt").write_text("n")
    client = make_client(tmp_path)
    with client:
        sid = new_session(client, ws)
        entries = client.get(f"/api/sessions/{sid}/fs/list").json()["entries"]
        names = [e["name"] for e in entries]
        # dirs first (alpha, case-insensitive), then files (alpha)
        assert names == ["A_dir", "b_dir", "a.txt", "z.txt"]
        assert entries[0]["type"] == "dir"
        txt = next(e for e in entries if e["name"] == "z.txt")
        assert txt["type"] == "file" and txt["size"] == 2 and "mtime" in txt
        # subdir listing via path
        sub = client.get(f"/api/sessions/{sid}/fs/list",
                         params={"path": "b_dir"}).json()["entries"]
        assert [e["name"] for e in sub] == ["nested.txt"]


def test_read_file(tmp_path):
    ws = workspace(tmp_path)
    (ws / "hello.txt").write_text("hello world")
    (ws / "pic.png").write_bytes(b"\x89PNG\r\n")
    client = make_client(tmp_path)
    with client:
        sid = new_session(client, ws)
        r = client.get(f"/api/sessions/{sid}/fs/file", params={"path": "hello.txt"})
        assert r.status_code == 200
        assert r.content == b"hello world"
        assert r.headers["content-type"].startswith("text/plain")
        img = client.get(f"/api/sessions/{sid}/fs/file", params={"path": "pic.png"})
        assert img.headers["content-type"] == "image/png"
        assert client.get(f"/api/sessions/{sid}/fs/file",
                          params={"path": "missing.txt"}).status_code == 404


def test_mkdir_touch(tmp_path):
    ws = workspace(tmp_path)
    client = make_client(tmp_path)
    with client:
        sid = new_session(client, ws)
        assert client.post(f"/api/sessions/{sid}/fs/mkdir",
                           json={"path": "a/b/c"}).json() == {"ok": True}
        assert (ws / "a" / "b" / "c").is_dir()
        assert client.post(f"/api/sessions/{sid}/fs/touch",
                           json={"path": "a/f.txt"}).json() == {"ok": True}
        assert (ws / "a" / "f.txt").is_file()
        # duplicate touch -> 409
        assert client.post(f"/api/sessions/{sid}/fs/touch",
                           json={"path": "a/f.txt"}).status_code == 409
        # mkdir where a file exists -> 409
        assert client.post(f"/api/sessions/{sid}/fs/mkdir",
                           json={"path": "a/f.txt"}).status_code == 409


def test_move(tmp_path):
    ws = workspace(tmp_path)
    (ws / "src.txt").write_text("x")
    (ws / "exists.txt").write_text("y")
    client = make_client(tmp_path)
    with client:
        sid = new_session(client, ws)
        assert client.post(f"/api/sessions/{sid}/fs/move",
                           json={"src": "src.txt", "dst": "d/dst.txt"}
                           ).json() == {"ok": True}
        assert (ws / "d" / "dst.txt").is_file()
        assert not (ws / "src.txt").exists()
        # missing src -> 404
        assert client.post(f"/api/sessions/{sid}/fs/move",
                           json={"src": "nope.txt", "dst": "x.txt"}
                           ).status_code == 404
        # existing dst -> 409
        assert client.post(f"/api/sessions/{sid}/fs/move",
                           json={"src": "d/dst.txt", "dst": "exists.txt"}
                           ).status_code == 409


def test_delete(tmp_path):
    ws = workspace(tmp_path)
    (ws / "f.txt").write_text("x")
    (ws / "d").mkdir()
    (ws / "d" / "n.txt").write_text("n")
    client = make_client(tmp_path)
    with client:
        sid = new_session(client, ws)
        assert client.post(f"/api/sessions/{sid}/fs/delete",
                           json={"path": "f.txt"}).json() == {"ok": True}
        assert not (ws / "f.txt").exists()
        assert client.post(f"/api/sessions/{sid}/fs/delete",
                           json={"path": "d"}).json() == {"ok": True}
        assert not (ws / "d").exists()
        # missing -> 404
        assert client.post(f"/api/sessions/{sid}/fs/delete",
                           json={"path": "gone"}).status_code == 404
        # root -> 400
        assert client.post(f"/api/sessions/{sid}/fs/delete",
                           json={"path": ""}).status_code == 400


def test_upload(tmp_path):
    ws = workspace(tmp_path)
    client = make_client(tmp_path)
    with client:
        sid = new_session(client, ws)
        r = client.post(
            f"/api/sessions/{sid}/fs/upload",
            data={"dir": "up"},
            files=[("files", ("a.txt", b"aaa", "text/plain")),
                   ("files", ("b.txt", b"bbbb", "text/plain"))])
        assert r.json() == {"ok": True}
        assert (ws / "up" / "a.txt").read_bytes() == b"aaa"
        assert (ws / "up" / "b.txt").read_bytes() == b"bbbb"
        # single into root
        r2 = client.post(
            f"/api/sessions/{sid}/fs/upload",
            data={"dir": ""},
            files=[("files", ("c.txt", b"c", "text/plain"))])
        assert r2.json() == {"ok": True}
        assert (ws / "c.txt").read_bytes() == b"c"


def test_traversal_rejected(tmp_path):
    ws = workspace(tmp_path)
    (ws / "ok.txt").write_text("x")
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    (ws / "escape").symlink_to(outside)
    client = make_client(tmp_path)
    with client:
        sid = new_session(client, ws)
        assert client.get(f"/api/sessions/{sid}/fs/file",
                          params={"path": "../secret.txt"}).status_code == 400
        assert client.get(f"/api/sessions/{sid}/fs/file",
                          params={"path": "/etc/passwd"}).status_code == 400
        # symlink inside cwd pointing outside -> 400
        assert client.get(f"/api/sessions/{sid}/fs/file",
                          params={"path": "escape"}).status_code == 400


def test_browse_dir(tmp_path):
    ws = workspace(tmp_path)
    (ws / "b_dir").mkdir()
    (ws / "A_dir").mkdir()
    (ws / "z.txt").write_text("hi")
    (ws / "a.txt").write_text("yo")
    client = make_client(tmp_path)
    with client:
        r = client.get("/api/fs/browse", params={"path": str(ws)})
        assert r.status_code == 200
        body = r.json()
        assert body["path"] == str(ws.resolve())
        assert body["parent"] == str(ws.resolve().parent)
        names = [e["name"] for e in body["entries"]]
        assert names == ["A_dir", "b_dir", "a.txt", "z.txt"]


def test_browse_default_home(tmp_path):
    client = make_client(tmp_path)
    with client:
        r = client.get("/api/fs/browse")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body["path"], str) and body["path"]


def test_browse_absolute_roundtrip(tmp_path):
    ws = workspace(tmp_path)
    (ws / "only.txt").write_text("x")
    client = make_client(tmp_path)
    with client:
        r = client.get("/api/fs/browse", params={"path": str(ws)})
        assert r.status_code == 200
        assert r.json()["path"] == str(ws.resolve())


def test_browse_not_a_directory(tmp_path):
    ws = workspace(tmp_path)
    (ws / "f.txt").write_text("x")
    client = make_client(tmp_path)
    with client:
        assert client.get("/api/fs/browse",
                          params={"path": str(ws / "f.txt")}).status_code == 400
        assert client.get("/api/fs/browse",
                          params={"path": str(ws / "nope")}).status_code == 400
