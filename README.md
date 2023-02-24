deforming autoencodersのリポジトリです．
## 学習するには
1. checkpointsに「wasp_model_epoch_decoders.pth」「wasp_model_epoch_encoders.pth」のファイルを追加する．  
2. 次のコマンドで学習開始  
```python3 train_DAE_CelebA.py --dirDataroot=./dataset/ --dirCheckpoints=./checkpoints --dirImageoutput=./Imag --dirTestingoutput=./Test```
3. どこに結果や学習した重みが保存されるか（追記する）  
## 各ファイルの説明
| th左 | th中央 | th右 |
| :-- | :-: | --: |
| td | td | td |