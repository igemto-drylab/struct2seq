import matplotlib.pyplot as plt

plt.rcParams["figure.dpi"] = 350

with open("loss_cath.txt", 'r') as f:
    train_loss = [float(line.split()[2]) for line in f.readlines() if line[0].isdigit()]
    test_loss = [float(line.split()[3]) for line in f.readlines() if line[0].isdigit()]

plt.plot(train_loss, label="Train")
plt.plot(test_loss, label="Test")
plt.legend()
plt.xlabel("Step")
plt.ylabel("Loss")
plt.show()