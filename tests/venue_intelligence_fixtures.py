import csv,json
from pathlib import Path
from mirror_fixtures import SyncCase,A,REPO,raw
from scholarflow.venue_intelligence import DEFAULT_FILES

SCOPUS_HEADER=['Sourcerecord ID','Source Title','ISSN','EISSN','Active or Inactive','Coverage','Titles Discontinued by Scopus','Article Language in Source (Three-Letter ISO Language Codes)','Medline-sourced Title? (See additional details under separate tab.)','Open Access Status','Articles in Press Included?','Added to List July 2026','Source Type','Title History Indication','Related Title 1','Other Related Title 2','Other Related Title 3','Other Related Title 4','Publisher','Publisher Imprints Grouped to Main Publisher','All Science Journal Classification Codes (ASJC)','Top level:\n\nLife Sciences','Top level:\n\nSocial Sciences','Top level:\n\nPhysical Sciences','Top level:\n\nHealth Sciences','1000 \nGeneral','1100\nAgricultural and Biological Sciences','1200\nArts and Humanities','1300\nBiochemistry, Genetics and Molecular Biology','1400\nBusiness, Management and Accounting','1500\nChemical Engineering','1600\nChemistry','1700\nComputer Science','1800\nDecision Sciences','1900\nEarth and Planetary Sciences','2000\nEconomics, Econometrics and Finance','2100\nEnergy','2200\nEngineering','2300\nEnvironmental Science','2400\nImmunology and Microbiology','2500\nMaterials Science','2600\nMathematics','2700\nMedicine','2800\nNeuroscience','2900\nNursing','3000\nPharmacology, Toxicology and Pharmaceutics','3100\nPhysics and Astronomy','3200\nPsychology','3300\nSocial Sciences','3400\nVeterinary','3500\nDentistry','3600\nHealth Professions']
WOS_HEADER=['Journal title','ISSN','eISSN','Publisher name','Publisher address','Languages','Web of Science Categories']


def write_csv(path,headers,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=headers); writer.writeheader()
        for row in rows: writer.writerow(dict.fromkeys(headers,'')|row)


def scopus_row(**kw):
    return {'Sourcerecord ID':'100','Source Title':'Alpha Journal','ISSN':'12345679','Active or Inactive':'Active','Coverage':'2026; 2023-2024','Source Type':'Journal','Publisher':'Synthetic Publisher','All Science Journal Classification Codes (ASJC)':'1407; 1701','Article Language in Source (Three-Letter ISO Language Codes)':'ENG; FRE','1400\nBusiness, Management and Accounting':'Business, Management and Accounting'}|kw


def wos_row(**kw):
    return {'Journal title':'Alpha Journal','ISSN':'1234-5679','Publisher name':'Synthetic Publisher','Publisher address':'Synthetic address','Languages':'English | French','Web of Science Categories':'Information Systems | Management'}|kw


class IntelligenceCase(SyncCase):
    def seed_lists(self,sha=A,scopus=None,wos=None):
        folder=self.root/'lists'/sha
        write_csv(folder/'SCOPUS/ext_list_Jul_2026.csv',SCOPUS_HEADER,[scopus_row()] if scopus is None else scopus)
        for collection in ['SCIE','SSCI','AHCI','ESCI']:
            rows=wos.get(collection,[]) if wos is not None else ([wos_row()] if collection in ['SCIE','SSCI'] else [])
            write_csv(folder/f'WOS/{collection}.csv',WOS_HEADER,rows)
        for name in ['SCOPUS/README.md','WOS/README.md']:
            p=folder/name; p.write_text('Synthetic documentation; not a runtime endpoint.')
        for name in DEFAULT_FILES: self.session.files[raw(sha,name)]=(folder/name).read_bytes()
        self.session.current=sha
        return folder
