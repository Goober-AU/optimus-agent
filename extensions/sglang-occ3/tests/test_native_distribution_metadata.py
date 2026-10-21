"""Static distribution contract; not an installed-wheel or image proof."""

from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def metadata():
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_generated_native_json_is_declared_package_data():
    package_data = metadata()["tool"]["setuptools"]["package-data"]
    assert package_data == {
        "occ3_sglang": ["native_profile.json", "native_sources.json"]
    }


def test_distribution_does_not_add_runtime_dependencies_or_select_startup():
    document = metadata()
    assert document["project"]["dependencies"] == []
    assert document["project"]["entry-points"]["sglang.srt.plugins"] == {
        "occ3_bridge": "occ3_sglang.plugin:load"
    }
    assert "EXPERIMENTAL_STARTUP" not in (ROOT / "pyproject.toml").read_text(
        encoding="utf-8"
    )


def test_description_does_not_claim_validated_radix_latent_transfer():
    description = metadata()["project"]["description"]
    assert "Experimental" in description
    assert "salted KV-radix" not in description


def test_readme_keeps_deployment_and_native_proof_gates_explicit():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "experimental and disabled by default" in readme
    assert "not proof of native latent communication" in readme
    assert "131072" in readme and "262144" in readme
    assert "authenticated native\nsource-tool provenance" in readme
