"""TEST DATA ONLY: schema boundary contract for HTML/PDF text anchors."""
import copy
import unittest
from rules_v2 import validate_catalog
from test_rules_v2 import minimal_catalog

class TextAnchorTests(unittest.TestCase):
    def test_text_anchors_accept_both_document_formats(self):
        for fmt in ['html', 'pdf']:
            catalog=minimal_catalog()
            e=catalog['programs'][0]['evidence'][0]
            e['format']=fmt
            e['anchors']['rate']['pointer']='/text'
            validate_catalog(catalog)

    def test_html_pdf_reject_other_pointers_for_the_expected_reason(self):
        for fmt in ['html', 'pdf']:
            for pointer in ['/body/text', '/text/0', '/', 'text']:
                with self.subTest(format=fmt,pointer=pointer):
                    catalog=minimal_catalog()
                    e=catalog['programs'][0]['evidence'][0]
                    e['format']=fmt
                    e['anchors']['rate']['pointer']=pointer
                    with self.assertRaisesRegex(ValueError,'HTML/PDF anchor pointer must be /text') as caught:
                        validate_catalog(catalog)
                    print(fmt,pointer,str(caught.exception))

    def test_aem_json_keeps_its_actual_json_pointer(self):
        catalog=minimal_catalog()
        validate_catalog(catalog)
        self.assertEqual(catalog['programs'][0]['evidence'][0]['anchors']['rate']['pointer'],'/a/0')

if __name__=='__main__': unittest.main()
