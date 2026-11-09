/* The C version of the homemade link probe; no platform headers or runtime startup. */
int result;

int add(int a, int b) {
    return a + b;
}

void start(void) {
    result = add(2, 3);
}
