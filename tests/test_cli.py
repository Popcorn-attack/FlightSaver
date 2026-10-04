from flightsaver.cli import main


def test_links_command(capsys):
    assert main(["links", "LON", "SHANGHAI", "2026-12-20", "--return", "2027-01-05"]) == 0
    out = capsys.readouterr().out
    assert "携程 Ctrip" in out and "British Airways" in out


def test_rejects_domestic(capsys):
    assert main(["links", "PEK", "PVG", "2026-12-20"]) == 2
    assert "UK <-> China" in capsys.readouterr().err


def test_search_with_fake_provider(monkeypatch, capsys, tmp_path):
    from conftest import make_offer

    import flightsaver.cli as cli

    class P:
        name = "fake"

        def search(self, q):
            return [make_offer(540)]

    monkeypatch.setattr(cli, "default_providers", lambda: [P()])
    db = tmp_path / "h.sqlite3"
    assert (
        main(["search", "LHR", "PVG", "2026-12-20", "--history", str(db), "--budget", "600"]) == 0
    )
    out = capsys.readouterr().out
    assert "540 GBP" in out and "[BUY" in out and "Trip.com" in out
