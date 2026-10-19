"""Build a tiny legal stand-in for CL.EXE, so generated PC builds can be exercised without Visual C++."""

import sys

import clr

clr.AddReference("Microsoft.CSharp")
from Microsoft.CSharp import CSharpCodeProvider  # noqa: E402
from System.CodeDom.Compiler import CompilerParameters  # noqa: E402

source = '''using System;
using System.IO;
class FakeCompiler {
    static int Main(string[] args) {
        File.WriteAllLines(Environment.GetEnvironmentVariable("DECOMP_TEST_CL_ARGS"), args);
        if (Environment.GetEnvironmentVariable("DECOMP_TEST_CL_FAIL") == "1") return 3;
        foreach (string arg in args) {
            if (arg.StartsWith("/Fo")) {
                string output = arg.Substring(3);
                Directory.CreateDirectory(Path.GetDirectoryName(output));
                File.WriteAllText(output, "object");
            }
        }
        return 0;
    }
}'''
options = CompilerParameters()
options.GenerateExecutable = True
options.OutputAssembly = sys.argv[1]
options.ReferencedAssemblies.Add("System.dll")
provider = CSharpCodeProvider()
try:
    result = provider.CompileAssemblyFromSource(options, source)
    assert not result.Errors.HasErrors, "\n".join(str(error) for error in result.Errors)
finally:
    provider.Dispose()
