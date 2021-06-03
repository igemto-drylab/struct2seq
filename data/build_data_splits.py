import os, time, gzip, json
import numpy as np
from mmtf_util import *

# CATH hierarchical classification
cath_base_url = 'http://download.cathdb.info/cath/releases/latest-release/'
cath_domain_fn = 'cath-domain-list.txt'
cath_domain_url = cath_base_url + 'cath-classification-data/' + cath_domain_fn
cath_domain_file = 'cath/cath-domain-list.txt'
download_cached(cath_domain_url, cath_domain_file)

# parse a chain_set with only those structures under the 3.40.50.1820 CATH label
chain_set = []
with open(cath_domain_file,'r') as f:
    lines = [line.strip() for line in f if not line.startswith('#')]
    for line in lines:
        entries = line.split()
        cath_id, cath_node = entries[0], '.'.join(entries[1:5])
        if cath_node == "3.40.50.1820" and cath_id[4] == 'A':       # only take chain A
            chain_set.append(f"{cath_id[:4]}.{cath_id[4]}")

N = len(chain_set)
train, val, test = [], [], []
for i in range(N):
    r = np.random.rand()
    if r < 0.8:
        train.append(chain_set[i])
    elif r < 0.9:
        val.append(chain_set[i])
    else:
        test.append(chain_set[i])

data_splits = {'test':test,
               'validation':val,
               'train':train}

with open('ec_data_splits.json', 'w') as fp:
    json.dump(data_splits, fp)
