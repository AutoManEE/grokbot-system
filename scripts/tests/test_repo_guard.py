import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import repo_guard as g
def test_blocks():
    assert g.scan("private/mail/x.csv", "")
    assert g.scan("a.txt", "mail me at someone@gmail.com")
    assert g.scan("a.txt", "gh" + "p_" + "A"*30)
    assert g.scan("a.txt", "-----BEGIN RSA PRIVATE KEY-----")
    assert g.scan("a.txt", "from " + "privkw" + "a daily summary")
    assert g.scan("alignment/_botlog_snapshot.txt", "")
def test_allows():
    assert not g.scan("scripts/x.py", "print('hello')")
    assert not g.scan("a.txt", "12345+owner@users.noreply.github.com")
