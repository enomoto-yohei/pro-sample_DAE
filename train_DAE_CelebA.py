from __future__ import print_function
import argparse
import os
import random
import torch
import torch.nn as nn
import torch.nn.parallel
import torch.backends.cudnn as cudnn
import torch.optim as optim
import torch.utils.data
import torchvision.datasets as dset
import torchvision.transforms as transforms
import torchvision.utils as vutils
from torch.autograd import Variable
from torch.autograd import gradcheck
from torch.autograd import Function
import math

# our data loader
import DAEDataLoader
import gc


import datetime
dt_now = datetime.datetime.now()
print("実行時の時刻",dt_now)



parser = argparse.ArgumentParser()
parser.add_argument('--workers', type=int, help='number of data loading workers', default=8)
parser.add_argument('--batchSize', type=int, default=100, help='input batch size')
parser.add_argument('--niter', type=int, default=25, help='number of epochs to train for')
parser.add_argument('--lr', type=float, default=0.0002, help='learning rate, default=0.0002')
parser.add_argument('--beta1', type=float, default=0.5, help='beta1 for adam. default=0.5')
parser.add_argument('--cuda', default = True, action='store_true', help='enables cuda')
parser.add_argument('--ngpu', type=int, default=1, help='number of GPUs to use')
parser.add_argument('--gpu_ids', type=int, default=0, help='ids of GPUs to use')
parser.add_argument('--manualSeed', type=int, help='manual seed')
parser.add_argument('--epoch_iter', type=int,default=6000, help='number of epochs on entire dataset')#600
parser.add_argument('--location', type = int, default=0, help ='where is the code running')
parser.add_argument('-f',type=str,default= '', help='dummy input required for jupyter notebook')
parser.add_argument('--modelPath', default='', help="path to model (to continue training)")#/mnt/test/TRY/DeformingAutoencoders-pytorch/checkpoints/qq
parser.add_argument('--dirCheckpoints', default='/nfs/bigdisk/zhshu/daeout/checkpoints/DAE_CelebA', help='folder to model checkpoints')
parser.add_argument('--dirImageoutput', default='/nfs/bigdisk/zhshu/daeout/images/DAE_CelebA', help='folder to output images')
parser.add_argument('--dirTestingoutput', default='/nfs/bigdisk/zhshu/daeout/testing/DAE_CelebA', help='folder to testing results/images')
parser.add_argument('--dirDataroot', default='/nfs/bigdisk/zhshu/data/wasp/', help='folder to dataroot')
parser.add_argument('--useDense', default = True, help='enables dense net architecture')
opt = parser.parse_args()

# size of image
opt.imgSize=64
opt.use_dropout = 0
opt.ngf = 32
opt.ndf = 32
# dimensionality: texture latent code
opt.idim = 16
# dimensionality: warping grid (deformation field) latent code
opt.wdim = 128
# dimensionality of general latent code (before disentangling)
opt.zdim = 128
opt.use_gpu = True
opt.gpu_ids = 0
opt.ngpu = 1
opt.nc = 3
#print("opt======\n",opt)

try:
    os.makedirs(opt.dirCheckpoints)#自分が指定したフォルダ
except OSError:
    pass
try:
    os.makedirs(opt.dirImageoutput)#自分が指定したフォルダ
except OSError:
    pass
try:
    os.makedirs(opt.dirTestingoutput)#自分が指定したフォルダ
except OSError:
    pass


if opt.manualSeed is None:
    opt.manualSeed = random.randint(1, 10000)
print("Random Seed: ", opt.manualSeed)
random.seed(opt.manualSeed)
torch.manual_seed(opt.manualSeed)
if opt.cuda:
    torch.cuda.manual_seed_all(opt.manualSeed)

cudnn.benchmark = True
if torch.cuda.is_available() and not opt.cuda:
    print("WARNING: You have a CUDA device, so you should probably run with --cuda")


def getBaseGrid(N=64, normalize = True, getbatch = False, batchSize = 1):
    print("N",N,"normalize",normalize,"getbatch",getbatch,"batchSize",batchSize)
    a = torch.arange(-(N-1), (N), 2)#(-(N-1), (N), 2)
    if normalize:
        a = a/(N-1.0)
    x = a.repeat(N,1)
    y = x.t()
    grid = torch.cat((x.unsqueeze(0), y.unsqueeze(0)),0)
    if getbatch:
        grid = grid.unsqueeze(0).repeat(batchSize,1,1,1)
    return grid

def weights_init(m):
    classname = m.__class__.__name__
    if classname.find('Conv') != -1:
        m.weight.data.normal_(0.0, 0.02)
    elif classname.find('BatchNorm') != -1:
        m.weight.data.normal_(1.0, 0.02)
        m.bias.data.fill_(0)

# sample iamges
def visualizeAsImages(img_list, output_dir, 
                      n_sample=4, id_sample=None, dim=-1, 
                      filename='myimage', nrow=2, 
                      normalize=False):
    if id_sample is None:
        images = img_list[0:n_sample,:,:,:]
    else:
        images = img_list[id_sample,:,:,:]
    if dim >= 0:
        images = images[:,dim,:,:].unsqueeze(1)
    vutils.save_image(images, 
        '%s/%s'% (output_dir, filename + '.png'),
        nrow=nrow, normalize = normalize, padding=2)

def parseSampledDataPoint(dp0_img, nc):
    dp0_img  = dp0_img.float()/255 # convert to float and rerange to [0,1]float に変換し、[0,1] に再配置します
    if nc==1:
        dp0_img  = dp0_img.unsqueeze(3)
    dp0_img  = dp0_img.permute(0,3,1,2).contiguous()  # reshape to [batch_size, 3, img_H, img_W][batch_size, 3, img_H, img_W] に変形
    return dp0_img


def setCuda(*args):
    barg = []
    for arg in args: 
        barg.append(arg.cuda())
    return barg

def setAsVariable(*args):
    barg = []
    for arg in args: 
        barg.append(Variable(arg))
    return barg   


# ---- The model ---- #
# get the model definition/architecture
# get network
#import DAENet
import DAENet_InstanceNorm as DAENet

if opt.useDense:
    encoders      = DAENet.Dense_Encoders(opt)
    decoders      = DAENet.Dense_DecodersIntegralWarper2(opt)
else:
    encoders      = DAENet.Encoders(opt)
    decoders      = DAENet.DecodersIntegralWarper2(opt)

if opt.cuda:
    encoders.cuda()
    decoders.cuda()

if not opt.modelPath=='':
    # rewrite here
    print("opt.modelPath",opt.modelPath)
    print('Reload previous model at: '+ opt.modelPath)
    encoders.load_state_dict(torch.load(opt.modelPath+'_encoders.pth'))
    decoders.load_state_dict(torch.load(opt.modelPath+'_decoders.pth'))
else:
    print('No previous model found, initializing model weight.')
    encoders.apply(weights_init)
    decoders.apply(weights_init)

print(opt.gpu_ids)
updator_encoders     = optim.Adam(encoders.parameters(), lr = opt.lr, betas=(opt.beta1, 0.999))
updator_decoders     = optim.Adam(decoders.parameters(), lr = opt.lr, betas=(opt.beta1, 0.999))

# criteria/loss
criterionRecon      = nn.L1Loss()
criterionTVWarp     = DAENet.TotalVaryLoss(opt)
criterionBiasReduce = DAENet.BiasReduceLoss(opt)
criterionSmoothL1   = DAENet.TotalVaryLoss(opt)
criterionSmoothL2   = DAENet.SelfSmoothLoss2(opt)

# Training set
TrainingData = []
TrainingData.append(opt.dirDataroot + 'celeba_split/img_00')#error___dirDataroot_自分が指定したフォルダ
TrainingData.append(opt.dirDataroot + 'celeba_split/img_01')
TrainingData.append(opt.dirDataroot + 'celeba_split/img_02')
TrainingData.append(opt.dirDataroot + 'celeba_split/img_03')
'''
TrainingData.append(opt.dirDataroot + 'celeba_split/img_04')
TrainingData.append(opt.dirDataroot + 'celeba_split/img_05')
TrainingData.append(opt.dirDataroot + 'celeba_split/img_06')
TrainingData.append(opt.dirDataroot + 'celeba_split/img_07')
TrainingData.append(opt.dirDataroot + 'celeba_split/img_08')
TrainingData.append(opt.dirDataroot + 'celeba_split/img_09')
TrainingData.append(opt.dirDataroot + 'celeba_split/img_10')
TrainingData.append(opt.dirDataroot + 'celeba_split/img_11')
TrainingData.append(opt.dirDataroot + 'celeba_split/img_12')
TrainingData.append(opt.dirDataroot + 'celeba_split/img_13')
TrainingData.append(opt.dirDataroot + 'celeba_split/img_14')
TrainingData.append(opt.dirDataroot + 'celeba_split/img_15')
TrainingData.append(opt.dirDataroot + 'celeba_split/img_16')
TrainingData.append(opt.dirDataroot + 'celeba_split/img_17')
TrainingData.append(opt.dirDataroot + 'celeba_split/img_18')
'''
# Testing set
TestingData = []
TestingData.append(opt.dirDataroot + 'celeba_split/img_19')


# ------------ training ------------ #
doTraining = True
doTesting = True
iter_mark=0
for epoch in range(opt.epoch_iter):
    train_loss = 0
    train_amount = 0+1e-6
    gc.collect() # collect garbage
    encoders.train()
    decoders.train()
    for dataroot in TrainingData:
        if not doTraining:
            break
        dataset = DAEDataLoader.DAEImageFolderResize(root=dataroot,rgb = True, resize = 64)
        print('# size of the current (sub)dataset is %d' %len(dataset))
        train_amount = train_amount + len(dataset)
        dataloader = torch.utils.data.DataLoader(dataset, batch_size=opt.batchSize, shuffle=True, num_workers=int(opt.workers))
        for batch_idx, data_point in enumerate(dataloader, 0):
            #raw_input("Press Enter to continue...")raw_input("続行するには Enter キーを押してください...")
            gc.collect() # collect garbageごみを集める
            ### prepare data ###データを準備する
            dp0_img = data_point
            dp0_img =  parseSampledDataPoint(dp0_img, opt.nc)
            baseg = getBaseGrid(N=opt.imgSize, getbatch = True, batchSize = dp0_img.size()[0])
            
            #print("baseg000\n",baseg.dtype)
            baseg = baseg.to(torch.float32)###new_add_reason_[int64(dtype Long)->float32]_change
            #print("baseg111\n",baseg.dtype)
            zeroWarp = torch.cuda.FloatTensor(1, 2, opt.imgSize, opt.imgSize).fill_(0)
            if opt.cuda:
                dp0_img, baseg, zeroWarp = setCuda(dp0_img, baseg, zeroWarp)
            dp0_img, = setAsVariable(dp0_img)
            baseg = Variable(baseg, requires_grad=False)
            #print("baseg222\n",baseg.dtype)
            zeroWarp = Variable(zeroWarp, requires_grad=False)
            updator_decoders.zero_grad()
            updator_encoders.zero_grad()
            decoders.zero_grad()
            encoders.zero_grad()
            ### forward training points: dp0フォワード トレーニング ポイント: dp0
            dp0_z, dp0_zI, dp0_zW = encoders(dp0_img)
            dp0_I, dp0_W, dp0_output, dp0_Wact = decoders(dp0_zI, dp0_zW, baseg)###error
            # reconstruction loss
            loss_recon = criterionRecon(dp0_output, dp0_img)
            # smooth warping loss
            loss_tvw = criterionTVWarp(dp0_W, weight=1e-6)
            # bias reduce loss
            loss_br = criterionBiasReduce(dp0_W, zeroWarp, weight=0.5e-1)#weight=1e-2<=====================
            # all loss functions
            loss_all = loss_recon + loss_tvw + loss_br
            loss_all.backward()

            updator_decoders.step()
            updator_encoders.step()
            #loss_encdec = loss_recon.data[0] + loss_br.data[0] + loss_tvw.data[0]###error
            loss_encdec = loss_recon.data.item() + loss_br.data[0] + loss_tvw.data[0]###error
            
            #########################################################################################
            f = open('./train/loss_encdec.txt', 'a')
            f.write(str(loss_encdec)+"\n")

            f = open('./train/loss_recon.data.item().txt', 'a')
            f.write(str(loss_recon.data.item())+"\n")

            f = open('./train/loss_tvw.data[0].txt', 'a')
            f.write(str(loss_tvw.data[0])+"\n")

            f = open('./train/loss_br.data[0].txt', 'a')
            f.write(str(loss_br.data[0])+"\n")
            #########################################################################################

            train_loss += loss_encdec
            
            iter_mark+=1
            print("\r",'Iteration[%d] loss -- all:  %.4f .. recon:  %.4f .. tvw: %.4f .. br: %.4f .. ' 
                #% (iter_mark,  loss_encdec, loss_recon.data[0], loss_tvw.data[0], loss_br.data[0]))
                % (iter_mark,  loss_encdec, loss_recon.data.item(), loss_tvw.data[0], loss_br.data[0]),end="")

            # visualzing training progressトレーニングの進捗状況を視覚化する
            gx = (dp0_W.data[:,0,:,:]+baseg.data[:,0,:,:]).unsqueeze(1).clone()###error
            gy = (dp0_W.data[:,1,:,:]+baseg.data[:,1,:,:]).unsqueeze(1).clone()
            visualizeAsImages(dp0_img.data.clone(), #入力画像
                opt.dirImageoutput, 
                filename='iter_'+str(iter_mark)+'_img0_', n_sample = 49, nrow=7, normalize=False)           
            visualizeAsImages(dp0_I.data.clone(), #再構築画像
                opt.dirImageoutput, 
                filename='iter_'+str(iter_mark)+'_tex0_', n_sample = 49, nrow=7, normalize=False)
            visualizeAsImages(dp0_output.data.clone(), #アピアランス
                opt.dirImageoutput, 
                filename='iter_'+str(iter_mark)+'_output0_', n_sample = 49, nrow=7, normalize=False)   
            visualizeAsImages((gx+1)/2, #変形場ｘ
                opt.dirImageoutput, 
                filename='iter_'+str(iter_mark)+'_warp0x_', n_sample = 49, nrow=7, normalize=False)          
            visualizeAsImages((gy+1)/2, #変形場ｙ
                opt.dirImageoutput, 
                filename='iter_'+str(iter_mark)+'_warp0y_', n_sample = 49, nrow=7, normalize=False)   
        if doTraining:
            # do checkpointing
            torch.save(encoders.state_dict(), '%s/wasp_model_epoch_encoders.pth' % (opt.dirCheckpoints))
            torch.save(decoders.state_dict(), '%s/wasp_model_epoch_decoders.pth' % (opt.dirCheckpoints))
    # ------------ testing ------------ #
    
    # on synthetic image set
    print('Testing images ... ')
    #raw_input("Press Enter to continue...")
    testing_loss=0
    gc.collect() # collect garbage
    for dataroot in TestingData:
        if not doTesting:
            break
        dataset = DAEDataLoader.DAEImageFolderResize(root=dataroot,rgb = True, resize = 64)##########
        print('# size of the current testing dataset is %d' %len(dataset))
        dataloader = torch.utils.data.DataLoader(dataset, batch_size=opt.batchSize, shuffle=True, num_workers=int(opt.workers))
        for batch_idx, data_point in enumerate(dataloader, 0):
            #raw_input("Press Enter to continue...")
            gc.collect() # collect garbage
            ### prepare data ###
            dp0_img = data_point
            dp0_img = parseSampledDataPoint(dp0_img, opt.nc)
            baseg = getBaseGrid(N=opt.imgSize, getbatch = True, batchSize = dp0_img.size()[0])
            baseg = baseg.to(torch.float32)
            zeroWarp = torch.cuda.FloatTensor(1, 2, opt.imgSize, opt.imgSize).fill_(0)
            if opt.cuda:
                dp0_img, baseg, zeroWarp = setCuda(dp0_img, baseg, zeroWarp)
            dp0_img, = setAsVariable(dp0_img)
            baseg = Variable(baseg, requires_grad=False)
            zeroWarp = Variable(zeroWarp, requires_grad=False)
            updator_decoders.zero_grad()
            updator_encoders.zero_grad()
            decoders.zero_grad()
            encoders.zero_grad()
            ### forward training points: dp0
            dp0_z, dp0_zI, dp0_zW = encoders(dp0_img)
            dp0_I, dp0_W, dp0_output, dp0_Wact = decoders(dp0_zI, dp0_zW, baseg)
            # reconstruction loss
            loss_recon = criterionRecon(dp0_output, dp0_img)
            # smooth warping loss
            loss_tvw = criterionTVWarp(dp0_W, weight=1e-6)
            # bias reduce loss
            loss_br = criterionBiasReduce(dp0_W, zeroWarp, weight=0.5e-1)#<========
            # all loss functions
            loss_all = loss_recon + loss_tvw + loss_br 

            loss_encdec = loss_recon.data.item() + loss_br.data[0] + loss_tvw.data[0] 

            testing_loss += loss_encdec

            #########################################################################################
            f = open('./test/loss_encdec.txt', 'a')
            f.write(str(loss_encdec)+"\n")

            f = open('./test/loss_recon.data.item().txt', 'a')
            f.write(str(loss_recon.data.item())+"\n")

            f = open('./test/loss_tvw.data[0].txt', 'a')
            f.write(str(loss_tvw.data[0])+"\n")

            f = open('./test/loss_br.data[0].txt', 'a')
            f.write(str(loss_br.data[0])+"\n")
            #########################################################################################



            print("\r",'Iteration[%d] loss -- all:  %.4f .. recon:  %.4f .. tvw: %.4f .. br: %.4f ' 
                % (iter_mark,  loss_encdec, loss_recon.data.item(), loss_tvw.data[0], loss_br.data[0]),end="")
        # visualzing training progress
        gx = (dp0_W.data[:,0,:,:]+baseg.data[:,0,:,:]).unsqueeze(1).clone()
        gy = (dp0_W.data[:,1,:,:]+baseg.data[:,1,:,:]).unsqueeze(1).clone()
        visualizeAsImages(dp0_img.data.clone(), 
            opt.dirTestingoutput, 
            filename='img0_', n_sample = 49, nrow=7, normalize=False)           
        visualizeAsImages(dp0_I.data.clone(), 
            opt.dirTestingoutput, 
            filename='tex0_', n_sample = 49, nrow=7, normalize=False)
        visualizeAsImages(dp0_output.data.clone(), 
            opt.dirTestingoutput, 
            filename='output0_', n_sample = 49, nrow=7, normalize=False)   
        visualizeAsImages((gx+1)/2, 
            opt.dirTestingoutput, 
            filename='warp0x_', n_sample = 49, nrow=7, normalize=False)          
        visualizeAsImages((gy+1)/2, 
            opt.dirTestingoutput, 
            filename='warp0y_', n_sample = 49, nrow=7, normalize=False)   
        # put testing code here #
    gc.collect() # collect garbage






























    ##
