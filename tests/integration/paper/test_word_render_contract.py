"""Safety preflight contracts for local Word rendering; these never launch Word."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "verify_word_render.ps1"
POWERSHELL = shutil.which("powershell.exe")
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


@unittest.skipUnless(os.name == "nt" and POWERSHELL, "Windows PowerShell required")
class WordRenderPreflightTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(os.environ["TEMP"])
        self.assertEqual(temp_root.drive.upper(), "F:")
        self.temp = tempfile.TemporaryDirectory(prefix="word-contract-", dir=temp_root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "input.docx"
        self.pdf = self.root / "render.pdf"
        self.write_package()

    def write_package(self, *, extra=None, body="<w:p><w:r><w:t>Safe</w:t></w:r></w:p>"):
        parts = {
            "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
            "word/document.xml": f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>',
        }
        parts.update(extra or {})
        with ZipFile(self.source, "w", ZIP_DEFLATED) as archive:
            for name, value in parts.items():
                archive.writestr(name, value)

    def preflight(self, output=None):
        return subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-STA", "-File", str(SCRIPT),
             "-InputDocx", str(self.source), "-OutputPdf", str(output or self.pdf),
             "-EvidenceDirectory", str(self.root), "-PreflightOnly"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
        )

    def assert_rejected(self, code):
        before = self.source.read_bytes()
        result = self.preflight()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(code, result.stdout + result.stderr)
        self.assertFalse(self.pdf.exists())
        self.assertEqual(before, self.source.read_bytes())

    def test_safe_preflight_does_not_launch_word_or_create_outputs(self):
        before = self.source.read_bytes()
        result = self.preflight()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout.strip())
        self.assertEqual(report["status"], "preflight_passed")
        self.assertFalse(report["word_started"])
        self.assertEqual([self.source], list(self.root.iterdir()))
        self.assertEqual(before, self.source.read_bytes())

    def test_rejects_non_zip_docx_with_clear_error(self):
        invalid_package = b"This is not a DOCX package."
        self.source.write_bytes(invalid_package)

        result = self.preflight()

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("DOCX_PACKAGE_INVALID", result.stdout + result.stderr)
        self.assertFalse(self.pdf.exists())
        self.assertEqual(invalid_package, self.source.read_bytes())

    def test_preflight_accepts_local_page_number_field(self):
        self.write_package(body=(
            '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            '<w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
            '<w:r><w:t>1</w:t></w:r>'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
        ))

        result = self.preflight()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout.strip())
        self.assertEqual(report["status"], "preflight_passed")
        self.assertFalse(self.pdf.exists())

    def test_rejects_non_f_output_before_creating_any_file(self):
        result = self.preflight("C:\\forbidden-word-output.pdf")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("F_PATH_REQUIRED", result.stdout + result.stderr)
        self.assertFalse(self.pdf.exists())

    def test_rejects_existing_output_without_overwriting(self):
        self.pdf.write_bytes(b"existing")
        result = self.preflight()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OUTPUT_EXISTS", result.stdout + result.stderr)
        self.assertEqual(b"existing", self.pdf.read_bytes())

    def test_rejects_external_relationship(self):
        self.write_package(extra={"word/_rels/document.xml.rels":
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="r1" Type="hyperlink" Target="https://example.invalid/" '
            'TargetMode="External"/></Relationships>'})
        self.assert_rejected("EXTERNAL_RELATIONSHIP")

    def test_rejects_macro_payload(self):
        self.write_package(extra={"word/vbaProject.bin": b"not executed"})
        self.assert_rejected("ACTIVE_CONTENT")

    def test_rejects_complex_field_instruction_even_when_split(self):
        self.write_package(body="<w:p><w:r><w:instrText>INCL</w:instrText></w:r>"
                           "<w:r><w:instrText>UDETEXT external</w:instrText></w:r></w:p>")
        self.assert_rejected("FIELD_NOT_SUPPORTED")

    def test_rejects_simple_field_instruction(self):
        self.write_package(body='<w:p><w:fldSimple w:instr="DDEAUTO external"/></w:p>')
        self.assert_rejected("FIELD_NOT_SUPPORTED")

    def test_rejects_xml_dtd(self):
        self.write_package(extra={"customXml/item1.xml":
            '<!DOCTYPE x [<!ENTITY e "untrusted">]><x>&e;</x>'})
        self.assert_rejected("UNSAFE_XML")

    def test_rejects_zip_member_traversal(self):
        self.write_package(extra={"../outside.xml": "<x/>"})
        self.assert_rejected("UNSAFE_MEMBER")


if __name__ == "__main__":
    unittest.main()


