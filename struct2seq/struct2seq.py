from __future__ import print_function

import numpy as np
from matplotlib import pyplot as plt
import copy

import torch
import torch.nn as nn
import torch.nn.functional as F

from .self_attention import *
from .protein_features import ProteinFeatures

from sklearn.decomposition import PCA
from sklearn.neighbors import KNeighborsClassifier

EMBDIM = 20
pca = PCA(n_components=EMBDIM)
embeds = np.loadtxt("aa.txt")
pca.fit(embeds)
JTVAE = pca.transform(embeds)
JTVAE = (JTVAE - np.min(JTVAE, axis=0)) / (np.max(JTVAE, axis=0) - np.min(JTVAE, axis=0))
JTVAE = 2*JTVAE - 1
JTVAE = torch.from_numpy(JTVAE)
JTVAE = JTVAE / np.linalg.norm(JTVAE, axis=1)

neigh = KNeighborsClassifier(n_neighbors=1)
neigh.fit(JTVAE, range(len(JTVAE)))

def _jtvae_to_seq(emb):
    alphabet = "ARNDCQEGHILKMFPSTWYV"
    S = neigh.predict(emb)
    seq = ''.join([alphabet[c] for c in S.tolist()])
    return seq

def _jtvae_to_index(emb):
    S = neigh.predict(emb)
    return S.flatten()

def closest_cosine(e):
    best = -1e6
    besti = 0
    for i, jt in enumerate(JTVAE):
        jt = jt.cpu().numpy().flatten()
        e = e.flatten()
        cossim = np.dot(jt, e) / (np.linalg.norm(jt) * np.linalg.norm(e))
        if cossim > best:
            besti = i
            best = cossim

    return besti

def _jtvae_to_index(emb):
    S = []
    for e in emb.cpu():
        S.append(closest_cosine(e))
    return np.array(S)

class Struct2Seq(nn.Module):
    def __init__(self, num_letters, node_features, edge_features,
        hidden_dim, num_encoder_layers=3, num_decoder_layers=3,
        vocab=20, k_neighbors=30, protein_features='full', augment_eps=0.,
        dropout=0.1, forward_attention_decoder=True, use_mpnn=False):
        """ Graph labeling network """
        super(Struct2Seq, self).__init__()

        # Hyperparameters
        self.node_features = node_features
        self.edge_features = edge_features
        self.hidden_dim = hidden_dim

        # Featurization layers
        self.features = ProteinFeatures(
            node_features, edge_features, top_k=k_neighbors,
            features_type=protein_features, augment_eps=augment_eps,
            dropout=dropout
        )

        # Embedding layers
        self.W_v = nn.Linear(node_features, hidden_dim, bias=True)
        self.W_e = nn.Linear(edge_features, hidden_dim, bias=True)
        # self.W_s = nn.Embedding(vocab, hidden_dim)
        self.W_s = nn.Linear(EMBDIM, hidden_dim)
        layer = TransformerLayer if not use_mpnn else MPNNLayer

        # Encoder layers
        self.encoder_layers = nn.ModuleList([
            layer(hidden_dim, hidden_dim*2, dropout=dropout)
            for _ in range(num_encoder_layers)
        ])

        # Decoder layers
        self.forward_attention_decoder = forward_attention_decoder
        self.decoder_layers = nn.ModuleList([
            layer(hidden_dim, hidden_dim*3, dropout=dropout)
            for _ in range(num_decoder_layers)
        ])
        # self.W_out = nn.Linear(hidden_dim, num_letters, bias=True)
        self.W_out = nn.Sequential(
            nn.Linear(hidden_dim, EMBDIM, bias=True),
            nn.Sigmoid(),
        )

        # Initialization
        for p in self.parameters():
            if p.dim() > 1:
                nn.init.xavier_uniform_(p)

    def _autoregressive_mask(self, E_idx):
        N_nodes = E_idx.size(1)
        ii = torch.arange(N_nodes)
        ii = ii.view((1, -1, 1))
        mask = E_idx < ii       # see plots if confused
        mask = mask.type(torch.float32)

        # Debug 
        # mask_scatter = torch.zeros(E_idx.shape[0],E_idx.shape[1],E_idx.shape[1]).scatter(-1, E_idx, mask)
        # mask_reduce = gather_edges(mask_scatter.unsqueeze(-1), E_idx).squeeze(-1)
        # plt.imshow(mask_reduce.data.numpy()[0,:,:])
        # plt.show()
        # plt.imshow(E_idx.data.cpu().numpy()[0,:,:])
        # plt.show()
        # plt.savefig("/content/E_idx.png", dpi=350)
        # plt.imshow(mask.data.cpu().numpy()[0,:,:])
        # plt.show()
        # plt.savefig("/content/mask.png", dpi=350)
        # print(E_idx[0,100,:])
        # print(mask[0,100,:])
        return mask

    # Only mask one index
    def bidirectional_mask(self, E_idx):
        N_nodes = E_idx.size(1)
        ii = torch.arange(N_nodes)
        ii = ii.view((1, -1, 1))
        mask = E_idx != ii
        mask = mask.type(torch.float32)
        return mask


    def forward(self, X, S, L, mask):
        """ Graph-conditioned sequence model """

        # Prepare node and edge embeddings
        V, E, E_idx = self.features(X, L, mask)
        h_V = self.W_v(V)
        h_E = self.W_e(E)

        # Encoder is unmasked self-attention
        mask_attend = gather_nodes(mask.unsqueeze(-1),  E_idx).squeeze(-1)
        mask_attend = mask.unsqueeze(-1) * mask_attend
        for layer in self.encoder_layers:
            h_EV = cat_neighbors_nodes(h_V, h_E, E_idx)
            h_V = layer(h_V, h_EV, mask_V=mask, mask_attend=mask_attend)

        # Concatenate sequence embeddings for autoregressive decoder
        h_S = self.W_s(S)
        h_ES = cat_neighbors_nodes(h_S, h_E, E_idx)

        # Build encoder embeddings
        h_ES_encoder = cat_neighbors_nodes(torch.zeros_like(h_S), h_E, E_idx)
        h_ESV_encoder = cat_neighbors_nodes(h_V, h_ES_encoder, E_idx)

        # Decoder uses masked self-attention
        # mask_attend = self._autoregressive_mask(E_idx).unsqueeze(-1)
        mask_attend = self.bidirectional_mask(E_idx).unsqueeze(-1)
        mask_1D = mask.view([mask.size(0), mask.size(1), 1, 1])
        mask_bw = mask_1D * mask_attend
        
        if self.forward_attention_decoder:
            # mask_fw = mask_1D * (1. - mask_attend)
            # h_ESV_encoder_fw = mask_fw * h_ESV_encoder
            h_ESV_encoder_fw = mask_bw * h_ESV_encoder
        else:
            h_ESV_encoder_fw = 0
        for layer in self.decoder_layers:
            # Masked positions attend to encoder information, unmasked see. 
            h_ESV = cat_neighbors_nodes(h_V, h_ES, E_idx)
            h_ESV = mask_bw * h_ESV + h_ESV_encoder_fw
            h_V = layer(h_V, h_ESV, mask_V=mask)

        logits = self.W_out(h_V) 
        # log_probs = F.log_softmax(logits, dim=-1)
        # return log_probs

        return logits / torch.norm(logits, dim=-1, keepdim=True)

    def sample(self, X, L, mask=None, temperature=1.0, S_gt=None):
        """ Autoregressive decoding of a model """
         # Prepare node and edge embeddings
        V, E, E_idx = self.features(X, L, mask)
        h_V = self.W_v(V)
        h_E = self.W_e(E)

        # Encoder is unmasked self-attention
        mask_attend = gather_nodes(mask.unsqueeze(-1),  E_idx).squeeze(-1)
        mask_attend = mask.unsqueeze(-1) * mask_attend
        for layer in self.encoder_layers:
            h_EV = cat_neighbors_nodes(h_V, h_E, E_idx)
            h_V = layer(h_V, h_EV, mask_V=mask, mask_attend=mask_attend)
        
        # Decoder alternates masked self-attention
        # mask_attend = self._autoregressive_mask(E_idx).unsqueeze(-1)
        mask_attend = self.bidirectional_mask(E_idx).unsqueeze(-1)
        mask_1D = mask.view([mask.size(0), mask.size(1), 1, 1])
        mask_bw = mask_1D * mask_attend
        mask_fw = mask_1D * (1. - mask_attend)
        N_batch, N_nodes = X.size(0), X.size(1)
        log_probs = torch.zeros((N_batch, N_nodes, 20))

        if S_gt is None:
            print("Sampling autoregressively")
            S = torch.zeros((N_batch, N_nodes), dtype=torch.int64)
        else:
            print("Starting from seed sequence")

        h_S = torch.zeros_like(h_V)
        h_V_stack = [h_V] + [torch.zeros_like(h_V) for _ in range(len(self.decoder_layers))]
        for t in range(N_nodes):
            # Hidden layers
            E_idx_t = E_idx[:,t:t+1,:]
            h_E_t = h_E[:,t:t+1,:,:]
            h_ES_t = cat_neighbors_nodes(h_S, h_E_t, E_idx_t)
            # Stale relational features for future states
            # h_ESV_encoder_t = mask_fw[:,t:t+1,:,:] * cat_neighbors_nodes(h_V, h_ES_t, E_idx_t)
            h_ESV_encoder_t = mask_bw[:,t:t+1,:,:] * cat_neighbors_nodes(h_V, h_ES_t, E_idx_t)

            for l, layer in enumerate(self.decoder_layers):
                # Updated relational features for future states
                h_ESV_decoder_t = cat_neighbors_nodes(h_V_stack[l], h_ES_t, E_idx_t)
                h_V_t = h_V_stack[l][:,t:t+1,:]
                h_ESV_t = mask_bw[:,t:t+1,:,:] * h_ESV_decoder_t + h_ESV_encoder_t
                h_V_stack[l+1][:,t,:] = layer(
                    h_V_t, h_ESV_t, mask_V=mask[:,t:t+1]
                ).squeeze(1)

            # Sampling step
            h_V_t = h_V_stack[-1][:,t,:]
            # logits = self.W_out(h_V_t) / temperature
            # probs = F.softmax(logits, dim=-1)
            # S_t = torch.multinomial(probs, 1).squeeze(-1)

            # Update
            # h_S[:,t,:] = self.W_s(S_t)
            # S[:,t] = S_t

            emb_out = self.W_out(h_V_t)
            
            h_S[:,t,:] = self.W_s(emb_out)
            S_t = _jtvae_to_index(emb_out)
            S_t = torch.from_numpy(S_t).type(torch.LongTensor)
            S[:,t] = S_t
            
        return S