/* A homemade, freestanding link probe. It is linked for inspection, not run as a Windows application. */
extern "C" {
int result;

int add(int a, int b) {
    return a + b;
}

void start() {
    result = add(2, 3);
}
}
