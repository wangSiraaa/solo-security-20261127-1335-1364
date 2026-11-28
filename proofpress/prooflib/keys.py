"""Ed25519 signatures via the OpenSSL CLI (mature system crypto library).

Keys are ordinary PEM files kept in local directories; the receiving seat
only ever gets the *public* half.  No state is shared with the sender.
"""

import os
import subprocess
import tempfile


def _run(args, data=None):
    proc = subprocess.run(
        ["openssl"] + args,
        input=data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise RuntimeError("openssl %s failed: %s" % (args[0], proc.stderr.decode()))
    return proc.stdout


def gen_keypair(priv_path, pub_path):
    os.makedirs(os.path.dirname(priv_path), exist_ok=True)
    _run(["genpkey", "-algorithm", "ed25519", "-out", priv_path])
    pub = _run(["pkey", "-in", priv_path, "-pubout"])
    with open(pub_path, "wb") as f:
        f.write(pub)


def sign(priv_path, data):
    with tempfile.NamedTemporaryFile(delete=False) as tf:
        tf.write(data)
        tmp = tf.name
    try:
        return _run(["pkeyutl", "-sign", "-inkey", priv_path, "-rawin", "-in", tmp])
    finally:
        os.unlink(tmp)


def verify(pub_path, data, sig):
    with tempfile.NamedTemporaryFile(delete=False) as tf:
        tf.write(data)
        td = tf.name
    with tempfile.NamedTemporaryFile(delete=False) as sf:
        sf.write(sig)
        ts = sf.name
    try:
        proc = subprocess.run(
            ["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", pub_path,
             "-rawin", "-in", td, "-sigfile", ts],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return proc.returncode == 0
    finally:
        os.unlink(td)
        os.unlink(ts)
