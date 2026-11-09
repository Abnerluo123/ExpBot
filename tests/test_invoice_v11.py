"""Printing layout preserves invoice facts, vector streams and immutable originals."""
from pathlib import Path
import hashlib
import io
import shutil
import unittest
import uuid
from PIL import Image,ImageChops
from app.config.settings import Settings
from app.core.invoice_layout import make_invoice_print_copy,invoice_image_page,invoice_options
from app.core.image_processor import process_voucher_layout
from app.core.pdf_generator import generate_invoice_pdf
from pypdf import PdfReader,PdfWriter
from pypdf.generic import DecodedStreamObject,NameObject,DictionaryObject

ROOT=Path(__file__).resolve().parents[1]
class InvoiceLayoutTests(unittest.TestCase):
    def setUp(self):
        self.root=ROOT/'output'/('invoice_v11_'+uuid.uuid4().hex[:10]);self.root.mkdir(parents=True)
        self.settings=Settings(pdf_dpi=100)
    def tearDown(self):
        assert self.root.resolve().parent==(ROOT/'output').resolve();shutil.rmtree(self.root)
    def vector_source(self,pages=1):
        writer=PdfWriter()
        for i in range(pages):
            page=writer.add_blank_page(595.28,510.24)
            content=DecodedStreamObject();content.set_data(b'0 0 0 rg 20 20 555 470 re S\nBT /F1 12 Tf 30 420 Td (SYNTHETIC TEST ONLY) Tj ET')
            font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
            page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
            page[NameObject('/Contents')]=writer._add_object(content)
        path=self.root/'original.pdf'
        with path.open('wb') as f:writer.write(f)
        return path
    def test_print_is_a4_landscape_with_guaranteed_margins(self):
        source=self.vector_source();before=source.read_bytes();target=self.root/'print.pdf'
        result=make_invoice_print_copy(source,target,self.settings)
        p=PdfReader(target).pages[0]
        self.assertAlmostEqual(float(p.mediabox.width)/72*25.4,297,places=2)
        self.assertAlmostEqual(float(p.mediabox.height)/72*25.4,210,places=2)
        self.assertEqual(before,source.read_bytes())
        self.assertTrue(result['vector_preserved'])
        self.assertFalse(result['signature_preserved_in_copy'])
        self.assertIn('SYNTHETIC TEST ONLY',p.extract_text())
        self.assertAlmostEqual(result['placements'][0]['scale'],.9,places=3)
        self.assertGreaterEqual(result['placements'][0]['bottom_mm'],24)
    def test_all_pages_retained(self):
        source=self.vector_source(3);target=self.root/'print.pdf'
        result=make_invoice_print_copy(source,target,self.settings)
        self.assertEqual(result['pages'],3);self.assertEqual(len(PdfReader(target).pages),3)
    def test_never_overwrites_original(self):
        source=self.vector_source();before=source.read_bytes()
        with self.assertRaises(ValueError):make_invoice_print_copy(source,source,self.settings)
        self.assertEqual(before,source.read_bytes())
    def test_settings_adjust_margins(self):
        self.settings.extra.update(invoice_top_mm=40,invoice_bottom_mm=35)
        r=make_invoice_print_copy(self.vector_source(),self.root/'custom.pdf',self.settings)
        self.assertGreaterEqual(r['placements'][0]['bottom_mm'],35)
        self.assertEqual(r['layout']['invoice_top_mm'],40)
    def test_invalid_margin_rejected(self):
        for value in ('abc',float('nan'),-1,101):
            with self.subTest(value=value),self.assertRaises(ValueError):invoice_options({'invoice_top_mm':value})
    def test_generator_defaults_to_print_not_original(self):
        source=self.vector_source()
        target,_,issues=generate_invoice_pdf(source,self.root/'export.pdf',settings=self.settings)
        self.assertNotEqual(target.read_bytes(),source.read_bytes())
        self.assertIn('invoice_print_layout',{x.code for x in issues})
    def test_explicit_archival_copy_retained(self):
        source=self.vector_source();self.settings.extra['invoice_layout_mode']='original'
        target,_,_=generate_invoice_pdf(source,self.root/'archive.pdf',settings=self.settings)
        self.assertEqual(target.read_bytes(),source.read_bytes())
    def test_image_invoice_has_top_bottom_white_bands(self):
        source=self.root/'source.png';Image.new('RGB',(210,180),'red').save(source)
        page=invoice_image_page(source,self.settings)
        ink=ImageChops.difference(page,Image.new('RGB',page.size,'white')).getbbox()
        self.assertGreaterEqual(ink[1]/page.height*210,23.8)
        self.assertGreaterEqual((page.height-ink[3])/page.height*210,23.8)
    def test_actual_sample_matches_reference_scale(self):
        source=ROOT/'examples/v1_1-input/原版发票.pdf'
        if not source.exists():self.skipTest('private sample not bundled')
        before=hashlib.sha256(source.read_bytes()).hexdigest()
        r=make_invoice_print_copy(source,self.root/'sample-print.pdf',self.settings)
        self.assertAlmostEqual(r['placements'][0]['scale'],.9,places=3)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),before)

class AdaptiveLayoutTests(unittest.TestCase):
    def setUp(self):
        self.root=ROOT/'output'/('adaptive_v11_'+uuid.uuid4().hex[:10]);self.root.mkdir(parents=True)
        self.pay=self.root/'pay.png';self.order=self.root/'order.png'
        Image.new('RGB',(300,1200),'blue').save(self.pay);Image.new('RGB',(900,1200),'red').save(self.order)
    def tearDown(self):
        assert self.root.resolve().parent==(ROOT/'output').resolve();shutil.rmtree(self.root)
    def test_unequal_ratios_use_natural_widths_and_equal_height(self):
        im=process_voucher_layout([self.order],[self.pay],dpi=100,crop_whitespace=False)
        m=im.info['layout_metrics'];self.assertAlmostEqual(m['column_ratio'],.75,places=2)
        heights=[p['box_pixels'][3] for p in m['placements']]
        self.assertLessEqual(abs(heights[0]-heights[1]),3)
    def test_ai_custom_ratio_respected_within_constraints(self):
        im=process_voucher_layout([self.order],[self.pay],dpi=100,crop_whitespace=False,layout={'layout_column_ratio':.35,'layout_vertical_align':'center'})
        self.assertEqual(im.info['layout_metrics']['column_ratio'],.35)
        self.assertEqual(im.info['layout_metrics']['vertical_align'],'center')
        for p in im.info['layout_metrics']['placements']:
            x,y,w,h=p['box_pixels'];self.assertGreaterEqual(x,round(24/25.4*100)-1)
            self.assertGreaterEqual(y,round(24/25.4*100)-1)
    def test_source_hashes_unchanged(self):
        before=[p.read_bytes() for p in (self.order,self.pay)]
        process_voucher_layout([self.order],[self.pay],dpi=100)
        self.assertEqual(before,[p.read_bytes() for p in (self.order,self.pay)])

if __name__=='__main__':unittest.main()
