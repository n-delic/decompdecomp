// A local headless worker: import once, then decompile functions at the boundaries supplied by the coordinator.
// Each header is applied in a rolled-back transaction, so one request's types never leak into another's.
import com.google.gson.*;
import ghidra.app.decompiler.*;
import ghidra.app.script.GhidraScript;
import ghidra.app.cmd.function.ApplyFunctionSignatureCmd;
import ghidra.app.util.cparser.C.CParser;
import ghidra.program.model.address.*;
import ghidra.program.model.data.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.SourceType;
import java.io.*;
import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.util.Map;

public class DecompWtfServe extends GhidraScript {
    private final Gson gson = new Gson();
    private void reply(JsonObject value) {
        System.out.println("DECOMP_REPLY:" + gson.toJson(value));
        System.out.flush();
    }
    private Function function(JsonObject r) throws Exception {
        Address at = toAddr(r.get("address").getAsLong());
        Address end = at.add(r.get("size").getAsLong() - 1);
        Function f = getFunctionAt(at);
        if (f != null) currentProgram.getFunctionManager().removeFunction(at);
        clearListing(at, end);
        if (r.has("thumb")) {
            ghidra.program.model.lang.Register reg = currentProgram.getRegister("TMode");
            if (reg != null) currentProgram.getProgramContext().setValue(reg, at, end,
                r.get("thumb").getAsBoolean() ? BigInteger.ONE : BigInteger.ZERO);
        }
        disassemble(at);
        f = currentProgram.getFunctionManager().createFunction(r.get("name").getAsString(), at,
            new AddressSet(at, end), SourceType.USER_DEFINED);
        return f;
    }
    private void bindings(JsonArray symbols) throws Exception {
        for (JsonElement item : symbols) {
            JsonObject s = item.getAsJsonObject();
            Address at = toAddr(s.get("address").getAsLong());
            String name = s.get("name").getAsString();
            if (currentProgram.getMemory().contains(at)) {
                createLabel(at, name, true, SourceType.USER_DEFINED);
                Function f = getFunctionAt(at);
                if (f != null) f.setName(name, SourceType.USER_DEFINED);
            }
        }
    }
    @Override public void run() throws Exception {
        // Shared ELF addresses are relative to zero. Keep the coordinator's addresses, independent of loader defaults.
        if (currentProgram.getImageBase().getOffset() != 0)
            throw new IllegalStateException("The ELF loader did not preserve the original zero-based addresses.");
        DecompInterface decompiler = new DecompInterface();
        DecompileOptions options = new DecompileOptions();
        options.setRespectReadOnly(true);
        decompiler.setOptions(options);
        for (ghidra.program.model.mem.MemoryBlock block : currentProgram.getMemory().getBlocks())
            if (block.isExecute()) block.setWrite(false);
        decompiler.openProgram(currentProgram);
        JsonObject ready = new JsonObject(); ready.addProperty("ready", true); reply(ready);
        BufferedReader input = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
        String line;
        while ((line = input.readLine()) != null && !monitor.isCancelled()) {
            JsonObject out = new JsonObject();
            int transaction = currentProgram.startTransaction("decompdecomp draft");
            try {
                JsonObject r = JsonParser.parseString(line).getAsJsonObject();
                if (r.has("quit")) break;
                out.addProperty("id", r.get("id").getAsLong());
                bindings(r.getAsJsonArray("symbols"));
                Function f = function(r);
                CParser parser = new CParser(currentProgram.getDataTypeManager());
                String header = r.get("header").getAsString();
                if (!header.isBlank()) parser.parse(new ByteArrayInputStream(header.getBytes(StandardCharsets.UTF_8)));
                for (Map.Entry<String, DataType> entry : parser.getFunctions().entrySet()) {
                    for (ghidra.program.model.symbol.Symbol symbol : currentProgram.getSymbolTable().getSymbols(entry.getKey())) {
                        Function callee = getFunctionAt(symbol.getAddress());
                        if (callee != null && entry.getValue() instanceof FunctionSignature)
                            new ApplyFunctionSignatureCmd(callee.getEntryPoint(), (FunctionSignature)entry.getValue(),
                                SourceType.USER_DEFINED).applyTo(currentProgram, monitor);
                    }
                }
                decompiler.flushCache();
                DecompileResults result = decompiler.decompileFunction(f, 30, monitor);
                if (result != null && result.decompileCompleted() && result.getDecompiledFunction() != null)
                    out.addProperty("c", result.getDecompiledFunction().getC());
                else out.addProperty("error", result == null ? "Ghidra returned no result" :
                    (result.getErrorMessage().isBlank() ? "The native decompiler could not produce C; check Ghidra's native components." : result.getErrorMessage()));
            } catch (Exception error) {
                out.addProperty("error", error.getMessage() == null ? error.toString() : error.getMessage());
            } finally {
                currentProgram.endTransaction(transaction, false);
                decompiler.flushCache();
            }
            reply(out);
        }
        decompiler.dispose();
    }
}
