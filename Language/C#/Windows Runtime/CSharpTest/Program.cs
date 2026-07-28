using System;
using System.IO;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using System.Linq;
using System.Text.Json;
using System.Net.Http;
using System.Security.Cryptography;
using System.Reflection;

class Programın
{
    static async Task Main()
    {
        Console.WriteLine("=== C# Library Integration Test ===");

        Test("System", true);
        Test("System.IO", TestIO());
        Test("System.Collections.Generic", TestCollections());
        Test("System.Threading", TestThread());
        Test("System.Threading.Tasks", await TestTask());
        Test("System.Linq", TestLinq());
        Test("System.Text.Json", TestJson());
        Test("System.Security.Cryptography", TestCrypto());
        Test("System.Reflection", TestReflection());

        Console.WriteLine();
        Console.WriteLine("=== Test Tamamlandı ===");
    }

    static void Test(string ad, bool sonuc)
    {
        Console.WriteLine(ad + ": " + (sonuc ? "Entegre" : "Hata"));
    }

    static bool TestIO()
    {
        string dosya = "test.txt";

        File.WriteAllText(dosya, "Göktürk");
        string veri = File.ReadAllText(dosya);
        File.Delete(dosya);

        return veri == "Göktürk";
    }

    static bool TestCollections()
    {
        List<int> liste = new List<int>();

        liste.Add(10);
        liste.Add(20);

        return liste.Count == 2;
    }

    static bool TestThread()
    {
        bool calisti = false;

        Thread t = new Thread(() =>
        {
            calisti = true;
        });

        t.Start();
        t.Join();

        return calisti;
    }

    static async Task<bool> TestTask()
    {
        await Task.Delay(10);

        return true;
    }

    static bool TestLinq()
    {
        List<int> sayilar = new List<int>
        {
            1,2,3,4,5
        };

        var sonuc = sayilar.Where(x => x > 3);

        return sonuc.Count() == 2;
    }

    static bool TestJson()
    {
        var veri = new
        {
            Dil = "Göktürk",
            Durum = "Test"
        };

        string json = JsonSerializer.Serialize(veri);

        return json.Contains("Göktürk");
    }

    static bool TestCrypto()
    {
        using SHA256 sha = SHA256.Create();

        byte[] sonuc = sha.ComputeHash(
            System.Text.Encoding.UTF8.GetBytes("Göktürk")
        );

        return sonuc.Length == 32;
    }

    static bool TestReflection()
    {
        Type tip = typeof(Program);

        return tip != null;
    }
}