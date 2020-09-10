#                                                            #
# This file is distributed as part of the WannierBerri code  #
# under the terms of the GNU General Public License. See the #
# file `LICENSE' in the root directory of the WannierBerri   #
# distribution, or http://www.gnu.org/copyleft/gpl.txt       #
#                                                            #
# The WannierBerri code is hosted on GitHub:                 #
# https://github.com/stepan-tsirkin/wannier-berri            #
#                     written by                             #
#           Stepan Tsirkin, University of Zurich             #
#                                                            #
#------------------------------------------------------------
#                                                            #
# author of this file: Patrick M. Lenggenhager               #
#                                                            #
#------------------------------------------------------------#

import numpy as np
from scipy import constants as constants
from collections import Iterable
import functools
from termcolor import cprint 

from . import __result as result

# constants
pi = constants.pi
e = constants.e
hbar = constants.hbar

# smearing functions
def Lorentzian(x, width):
    return 1.0/(pi*width) * width**2/(x**2 + width**2)
def Gaussian(x, width):
    # Compute 1 / (np.sqrt(pi) * width) * exp(-(x / width) ** 2)
    # If the exponent is less than -200, return 0.
    inds = abs(x) < width * np.sqrt(200.0)
    output = np.zeros_like(x)
    output[inds] = 1.0 / (np.sqrt(pi) * width) * np.exp(-(x[inds] / width)**2)
    return output

# Fermi-Dirac distribution
def FermiDirac(E, mu, kBT):
    if kBT == 0:
        return 1.0*(E <= mu)
    else:
        arg = np.maximum(np.minimum((E-mu)/kBT, 700.0), -700.0)
        return 1.0/(np.exp(arg) + 1)

def kubo_sum_elements(x, y, num_wann):
    # Compute np.einsum('mnab,wnm->wab', x, y).
    # This implementation is much faster than calling np.einsum.
    assert x.shape == (num_wann, num_wann, 3, 3)
    assert y.shape[1] == num_wann
    assert y.shape[2] == num_wann
    x_reshape = x.reshape((num_wann**2, 3 * 3))
    y_reshape = y.reshape((-1, num_wann**2))
    return (y_reshape @ x_reshape).reshape((-1, 3, 3))


def opt_conductivity(data, omega=0, mu=0, kBT=0, smr_fixed_width=0.1, smr_type='Lorentzian', adpt_smr=False,
                adpt_smr_fac=np.sqrt(2), adpt_smr_max=0.1, adpt_smr_min=1e-15, conductivity_type='kubo'):
    '''
    Calculates the optical conductivity according to the Kubo-Greenwood formula.

    Arguments:
        data            instance of __data_dk.Data_dk representing a single point in the BZ
        omega           value or list of frequencies in units of eV/hbar
        mu              chemical potential in units of eV/hbar
        kBT             temperature in units of eV/kB
        smr_fixed_width smearing paramters in units of eV
        smr_type        analytical form of broadened delta function ('Gaussian' or 'Lorentzian')
        adpt_smr        specifies whether to use an adaptive smearing parameter (for each pair of states)
        adpt_smr_fac    prefactor for the adaptive smearing parameter
        adpt_smr_max    maximal value of the adaptive smearing parameter
        adpt_smr_min    minimal value of the adaptive smearing parameter

    Returns:    a list of (complex) optical conductivity 3 x 3 tensors (one for each frequency value).
                The result is given in S/cm.
    '''

    # data gives results in terms of
    # ik = index enumerating the k points in Data_dk object
    # m,n = indices enumerating the eigenstates/eigenvalues of H(k)
    # a,b = cartesian coordinate
    # additionally the result will include
    # iw = index enumerating the frequency values
    # ri = index for real and imaginary parts (0 -> real, 1 -> imaginary)

    # TODO: optimize for T = 0? take only necessary elements



    # frequency
    if not isinstance(omega, Iterable):
        omega = np.array([omega])

    if conductivity_type == 'kubo':    
        sigma_H = np.zeros((omega.shape[0], 3, 3), dtype=np.dtype('complex128'))
        sigma_AH = np.zeros((omega.shape[0], 3, 3), dtype=np.dtype('complex128'))
        rank=2
    elif conductivity_type == 'SHC':
        sigma_H = np.zeros((omega.shape[0], 3, 3, 3), dtype=np.dtype('complex128'))
        sigma_AH = np.zeros((omega.shape[0], 3, 3, 3), dtype=np.dtype('complex128'))
        rank=3

    # prefactor for correct units of the result (S/cm)
    pre_fac = e**2/(100.0 * hbar * data.NKFFT_tot * data.cell_volume * constants.angstrom)

    # iterate over ik, simple summation
    for ik in range(data.NKFFT_tot):
        # energy
        E = data.E_K[ik] # energies [n] in eV
        dE = E[np.newaxis,:] - E[:, np.newaxis] # E_m(k) - E_n(k) [n, m]

         # occupation
        fE = FermiDirac(E, mu, kBT) # f(E_m(k)) - f(E_n(k)) [n]
        dfE = fE[np.newaxis,:] - fE[:, np.newaxis] # [n, m]

        if conductivity_type == 'kubo':
            # generalized Berry connection matrix
            A = data.A_H[ik] # [n, m, a] in angstrom
            B = data.A_H[ik]
        elif conductivity_type == 'SHC':
            B = - 1j*data.A_H[ik]
            if data.SH_R is not None:
                A = 0.5 * (data.B_H[ik] + data.B_H[ik].transpose(1,0,2,3).conj())
            else:
                # PRB RPS19
                VV = data.V_H[ik] # [n,m,a]
                SS = data.S_H[ik]   # [n,m,b]
                SA = data.SA_H[ik]  # [n,m,a,b]
                SHA = data.SHA_H[ik]# [n,m,a,b]
                A = np.einsum('nla,lmb->nmab',VV,SS)+np.einsum('nlb,lma->nmab',SS,VV)
                A += -1j * (E[np.newaxis,:,np.newaxis,np.newaxis]*SA - SHA)
                SA_adj = SA.transpose(1,0,2,3).conj()
                SHA_adj = SHA.transpose(1,0,2,3).conj()
                A += 1j *  (E[:,np.newaxis,np.newaxis,np.newaxis]*SA_adj - SHA_adj)
                A /= 2.0

        # E - omega
        delta_arg = dE[np.newaxis,:,:] - omega[:,np.newaxis,np.newaxis] # argument of delta function [iw, n, m]

        # smearing
        if adpt_smr: # [iw, n, m]
            #cprint("Adaptive smearing is an experimental feature and has not been extensively tested.", 'red')
            eta = smr_fixed_width
            delE = data.delE_K[ik] # energy derivatives [n, a] in eV*angstrom
            ddelE = delE[np.newaxis,:] - delE[:, np.newaxis] # delE_m(k) - delE_n(k) [n, m, a]
            eta = np.maximum(adpt_smr_min, np.minimum(adpt_smr_max,
                adpt_smr_fac * np.linalg.norm(ddelE, axis=2) * np.max(data.Kpoint.dK_fullBZ)))[np.newaxis, :, :]
        else:
            eta = smr_fixed_width # number

        # Hermitian part of the conductivity tensor
        # broadened delta function [iw, n, m]
        if smr_type == 'Lorentzian':
            delta = Lorentzian(delta_arg, eta)
        elif smr_type == 'Gaussian':
            delta = Gaussian(delta_arg, eta)
        else:
            cprint("Invalid smearing type. Fallback to Lorentzian", 'red')
            delta = Lorentzian(delta_arg, eta)

        if conductivity_type == 'kubo':
            # real part of energy fraction [iw, n, m]
            re_efrac = delta_arg/(delta_arg**2 + eta**2)

            # temporary variables for computing conductivity tensor
            tmp1 = dfE * dE
            tmp2 = np.einsum('nma,mnb->nmab', A, A)
            tmp3 = tmp1[:, :, np.newaxis, np.newaxis] * tmp2

            # Hermitian part of the conductivity tensor
            sigma_H += -1 * pi * pre_fac * kubo_sum_elements(tmp3, delta, data.num_wann)
            # anti-Hermitian part of the conductivity tensor
            sigma_AH += 1j * pre_fac * kubo_sum_elements(tmp3, re_efrac, data.num_wann)

        elif conductivity_type == 'SHC':
            delta_minus = np.copy(delta)
            delta_arg = dE[np.newaxis,:,:] + omega[:,np.newaxis,np.newaxis]
            if smr_type == 'Lorentzian':
                delta = Lorentzian(delta_arg, eta)
            elif smr_type == 'Gaussian':
                delta = Gaussian(delta_arg, eta)
            else:
                cprint("Invalid smearing type. Fallback to Lorentzian", 'red')
                delta = Lorentzian(delta_arg, eta)
            delta_plus = np.copy(delta)
            cfac2 = delta_minus - delta_plus
            cfac1 = np.real(-dE[np.newaxis,:,:]/(dE[np.newaxis,:,:]**2-(omega[:,np.newaxis,np.newaxis]+1j*eta)**2))
            temp1 = dfE[np.newaxis,:,:]*cfac1
            temp2 = dfE[np.newaxis,:,:]*cfac2
            imAB = np.imag(np.einsum('nmac,mnb->nmabc',A,B))
            sigma_H += 1j * pi * pre_fac * np.einsum('wnm,nmabc->wabc',temp2,imAB) / 4.0
            sigma_AH += pre_fac * np.einsum('wnm,nmabc->wabc',temp1,imAB) / 2.0

    # TODO: optimize by just storing independent components or leave it like that?
    # 3x3 tensors [iw, a, b] or [iw,a,b,c]
    sigma_sym = np.real(sigma_H) + 1j * np.imag(sigma_AH) # symmetric (TR-even, I-even)
    sigma_asym = np.real(sigma_AH) + 1j * np.imag(sigma_H) # ansymmetric (TR-odd, I-even)

    # return result dictionary
    return result.EnergyResultDict({
        'sym':  result.EnergyResult(omega, sigma_sym, TRodd=False, Iodd=False, rank=rank),
        'asym': result.EnergyResult(omega, sigma_asym, TRodd=False, Iodd=False, rank=rank)
    }) # the proper smoother is set later for both elements


def opt_SHC(data, omega=0, mu=0, kBT=0, smr_fixed_width=0.1, smr_type='Lorentzian', adpt_smr=False,
                adpt_smr_fac=np.sqrt(2), adpt_smr_max=0.1, adpt_smr_min=1e-15):
    return opt_conductivity(data, omega, mu, kBT, smr_fixed_width, smr_type, adpt_smr,
                adpt_smr_fac, adpt_smr_max, adpt_smr_min, conductivity_type='SHC')