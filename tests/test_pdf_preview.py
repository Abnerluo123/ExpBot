"""Windows desktop scaling must not silently multiply PDF preview memory."""
import shutil,unittest,uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image
from app.core.pdf_utils import render_pdf_page

class PDFPreviewScaleTests(unittest.TestCase):
    def test_200_percent_windows_display_is_normalized_to_requested_pixels(self):
        root=Path(__file__).resolve().parents[1]/'output'/('pdf-scale-'+uuid.uuid4().hex)
        root.mkdir(parents=True)
        self.addCleanup(lambda:shutil.rmtree(root))
        original=root/'immutable.pdf';original.write_bytes(b'%PDF-placeholder-for-renderer-mock')
        target=root/'preview.png'
        def render(*args,**kwargs):
            with Image.new('RGB',(800,600),'white') as image:image.save(target)
            Path(str(target)+'.status.txt').write_text('OK pages=3 width=100 height=75',encoding='utf-8')
            return SimpleNamespace(returncode=0)
        with patch('app.core.pdf_utils.subprocess.run',side_effect=render):
            result,count=render_pdf_page(original,target,4.0,1)
        self.assertEqual(count,3)
        with Image.open(result) as image:self.assertEqual(image.size,(400,300))
        self.assertEqual(original.read_bytes(),b'%PDF-placeholder-for-renderer-mock')

if __name__=='__main__':unittest.main()
