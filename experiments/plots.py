import matplotlib.pyplot as plt

plt.rcParams["figure.dpi"] = 350

with open("loss.txt", 'r') as f:
    loss = [float(line.split()[2]) for line in f.readlines()]

plt.plot(loss)
plt.xlabel("Step")
plt.ylabel("Loss")
plt.show()