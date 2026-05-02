"""
store.py Redis tabanlı job store testleri.
conftest.py'deki fake_redis_store fixture'ı her test için temiz FakeRedis sağlar.
"""

from app import store


class TestCrud:
    def test_create_and_get(self):
        store.create("job-1", {"name": "test"})
        job = store.get("job-1")
        assert job is not None
        assert job["status"] == "pending"
        assert job["name"] == "test"

    def test_get_nonexistent(self):
        assert store.get("nonexistent") is None

    def test_set_running(self):
        store.create("job-2", {})
        store.set_running("job-2")
        assert store.get("job-2")["status"] == "running"

    def test_set_done(self):
        store.create("job-3", {})
        store.set_done("job-3", {"total_score": 75.0})
        j = store.get("job-3")
        assert j["status"] == "done"
        assert j["result"]["total_score"] == 75.0

    def test_set_failed(self):
        store.create("job-4", {})
        store.set_failed("job-4", "GEE timeout")
        j = store.get("job-4")
        assert j["status"] == "failed"
        assert "GEE timeout" in j["error"]

    def test_list_all(self):
        store.create("j1", {"name": "a"})
        store.create("j2", {"name": "b"})
        items = store.list_all()
        ids = {i["id"] for i in items}
        assert {"j1", "j2"}.issubset(ids)

    def test_set_running_unknown_id_noop(self):
        store.set_running("ghost")  # patlamamalı

    def test_set_done_unknown_id_noop(self):
        store.set_done("ghost", {})  # patlamamalı

    def test_batch_update_progress(self):
        store.create("batch-1", {"total_locations": 3})
        store.batch_update_progress("batch-1", 2, [{"score": 80}])
        j = store.get("batch-1")
        assert j["completed"] == 2
        assert len(j["results"]) == 1


class TestThreadSafety:
    def test_concurrent_creates(self):
        import threading
        errors = []

        def create_job(i):
            try:
                store.create(f"concurrent-{i}", {"name": str(i)})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=create_job, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        items = store.list_all()
        ids = {i["id"] for i in items}
        assert all(f"concurrent-{i}" in ids for i in range(20))
