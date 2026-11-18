using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using System;
using System.ComponentModel.DataAnnotations;
using System.IO;
using System.Threading.Tasks;

namespace Online;

public partial class ExternalidsContext
{
    public static IDbContextFactory<ExternalidsContext> Factory { get; set; }

    public static void Initialization(IServiceProvider applicationServices)
    {
        Directory.CreateDirectory("cache");

        Factory = applicationServices.GetService<IDbContextFactory<ExternalidsContext>>();

        using (var sqlDb = new ExternalidsContext())
            sqlDb.Database.EnsureCreated();
    }

    public async Task SaveMapping(string id, string value)
    {
        try
        {
            // EF maps both DbSet properties to this existing table.
            await Database.ExecuteSqlInterpolatedAsync($"""
                INSERT INTO ExternalidsSqlModel (Id, value) VALUES ({id}, {value})
                ON CONFLICT(Id) DO UPDATE SET value = excluded.value
                WHERE ExternalidsSqlModel.value IS NULL OR trim(ExternalidsSqlModel.value) IN ('', '0')
                """);
        }
        catch (SqliteException ex)
        {
            Serilog.Log.Warning("Cannot save external ID mapping in SQLite (code {Code})", ex.SqliteErrorCode);
        }
    }
}


public partial class ExternalidsContext : DbContext
{
    public DbSet<ExternalidsSqlModel> imdb { get; set; }

    public DbSet<ExternalidsSqlModel> kinopoisk { get; set; }

    static readonly string _connection = new SqliteConnectionStringBuilder
    {
        DataSource = "cache/Externalids.sql",
        Cache = SqliteCacheMode.Shared,
        DefaultTimeout = 10,
        Pooling = true
    }.ToString();

    public static void ConfiguringDbBuilder(DbContextOptionsBuilder optionsBuilder)
    {
        if (!optionsBuilder.IsConfigured)
        {
            optionsBuilder.UseSqlite(_connection);
            optionsBuilder.UseQueryTrackingBehavior(QueryTrackingBehavior.NoTracking);
        }
    }

    protected override void OnConfiguring(DbContextOptionsBuilder optionsBuilder)
    {
        ConfiguringDbBuilder(optionsBuilder);
    }
}

public class ExternalidsSqlModel
{
    [Key]
    public string Id { get; set; }

    public string value { get; set; }
}
