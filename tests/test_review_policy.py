"""Four requested review categories and duplicate-warning regression."""
import unittest
from app.core.review_policy import category_of,compact_issues

class ReviewPolicyTests(unittest.TestCase):
    def test_only_requested_categories_remain(self):
        cases={'buyer_name':'personal_info','taxpayer_id':'personal_info','address':'address','missing_invoice':'missing_materials','clarity':'clarity'}
        for code,expected in cases.items():
            self.assertEqual(category_of({'code':code,'message':'需要检查'}),expected)
        for code in ('amount_mismatch','amount_unknown','merchant_alias','stitch_alternate_view','border_crop','multi_invoice','invoice_from_image'):
            self.assertIsNone(category_of({'code':code,'message':'原始记录保留'}))

    def test_duplicate_checks_combine_per_purchase_and_category(self):
        rows=[{'code':'buyer','group_key':'g1','message':'抬头不符','level':'error'},
              {'code':'tax_id','group_key':'g1','message':'税号不符','level':'warning'},
              {'code':'address','group_key':'g1','message':'收货地址不完整'},
              {'code':'buyer','group_key':'g2','message':'抬头不符','level':'error'}]
        out=compact_issues(rows)
        self.assertEqual(len(out),3)
        self.assertIn('抬头不符',out[0]['message']);self.assertIn('税号不符',out[0]['message'])
        self.assertEqual(out[0]['category'],'personal_info')
        self.assertEqual(out[0]['level'],'error')

    def test_pass_and_ordinary_metadata_do_not_become_warnings(self):
        self.assertEqual(compact_issues([{'code':'clarity','status':'pass','message':'清晰'},'边框未裁，原件保留','主单和子单编号不同','订单金额不同']),[])

if __name__=='__main__':unittest.main()
