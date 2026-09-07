import re
import time
from datetime import date, timedelta
from typing import Iterable

import pandas as pd
import requests
from Bio import Entrez

from config import UK_HEIS, OPENALEX_JOURNAL_ALIASES


STANDARD_COLUMNS = [
    "Author", "ORCID", "University", "Region", "Country", "Affiliation",
    "Publication Date", "Publication Year", "Journal", "Research Title",
    "Publication ID", "DOI", "Citations", "Source Database", "Record URL"
]


def normalise_text(value):
    value = str(value or "").lower().replace("’", "'").replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


HEI_ALIAS_LOOKUP = []
for item in UK_HEIS:
    for alias in item["Aliases"]:
        HEI_ALIAS_LOOKUP.append({
            "alias": normalise_text(alias),
            "University": item["University"],
            "Region": item["Region"],
            "Country": item["Country"],
        })

HEI_ALIAS_LOOKUP.sort(key=lambda item: len(item["alias"]), reverse=True)


def alias_matches(alias, text):
    if not alias:
        return False
    if " " not in alias and len(alias) <= 6:
        return re.search(rf"\b{re.escape(alias)}\b", text) is not None
    return alias in text


def match_hei(value):
    text = normalise_text(value)
    for item in HEI_ALIAS_LOOKUP:
        if alias_matches(item["alias"], text):
            return item["University"], item["Region"], item["Country"]
    return None, None, None


def clean_orcid(value):
    if not value:
        return None
    value = str(value).strip().replace("https://orcid.org/", "").replace("http://orcid.org/", "")
    return value or None


def date_to_iso(value):
    if value is None:
        return None
    return pd.Timestamp(value).date().isoformat()


def build_pubmed_journal_clause(journals):
    if not journals:
        return ""
    parts = [f'"{str(journal).replace(chr(34), "")}"[Journal]' for journal in journals]
    return "(" + " OR ".join(parts) + ")"


def build_pubmed_base_query(search_query, journals):
    # Build the non-date part once so it can be reused for date chunks.
    clauses = []

    if journals:
        clauses.append(build_pubmed_journal_clause(journals))

    if search_query.strip():
        clauses.append(f"({search_query.strip()})")

    return " AND ".join(clauses)


def build_pubmed_query(search_query, start_date, end_date, journals):
    base_query = build_pubmed_base_query(search_query, journals)
    start_text = pd.Timestamp(start_date).strftime("%Y/%m/%d")
    end_text = pd.Timestamp(end_date).strftime("%Y/%m/%d")
    date_clause = f'("{start_text}"[PDAT] : "{end_text}"[PDAT])'

    return f"{base_query} AND {date_clause}" if base_query else date_clause


def _configure_entrez(api_key=None, email=None):
    if api_key:
        Entrez.api_key = api_key

    if email:
        Entrez.email = email


def _pubmed_count(query, api_key=None, email=None):
    _configure_entrez(api_key=api_key, email=email)

    handle = Entrez.esearch(db="pubmed", term=query, retmax=0)
    record = Entrez.read(handle)
    handle.close()

    return int(record.get("Count", 0))


def _pubmed_ids_for_query(query, requested, api_key=None, email=None):
    _configure_entrez(api_key=api_key, email=email)

    if requested <= 0:
        return []

    handle = Entrez.esearch(db="pubmed", term=query, retmax=int(requested), sort="pub date")
    record = Entrez.read(handle)
    handle.close()

    return list(record.get("IdList", []))


def _collect_pubmed_ids(search_query, start_date, end_date, journals, max_publications=None, api_key=None, email=None):
    # PubMed ESearch exposes at most the first 10,000 results for one query.
    # Split large searches into smaller date windows and merge the PMIDs.
    start_date = pd.Timestamp(start_date).date()
    end_date = pd.Timestamp(end_date).date()

    full_query = build_pubmed_query(search_query, start_date, end_date, journals)
    total_hits = _pubmed_count(full_query, api_key=api_key, email=email)

    limit = None if max_publications in (None, 0, "all") else int(max_publications)
    per_query_limit = 9999

    collected_pmids = []
    seen_pmids = set()
    windows_searched = 0
    single_day_truncations = 0

    def remaining_limit():
        if limit is None:
            return None
        return max(limit - len(collected_pmids), 0)

    def collect_window(window_start, window_end):
        nonlocal windows_searched, single_day_truncations

        remaining = remaining_limit()
        if remaining == 0:
            return

        query = build_pubmed_query(search_query, window_start, window_end, journals)
        count = _pubmed_count(query, api_key=api_key, email=email)
        windows_searched += 1

        if count == 0:
            return

        # Keep splitting until the window is small enough for complete PMID retrieval.
        if count > per_query_limit and window_start < window_end:
            midpoint = window_start + (window_end - window_start) // 2
            collect_window(window_start, midpoint)

            if remaining_limit() != 0:
                collect_window(midpoint + timedelta(days=1), window_end)

            return

        # A single day with >9,999 hits cannot be split any further by publication date.
        if count > per_query_limit and window_start == window_end:
            single_day_truncations += 1

        requested = min(count, per_query_limit)
        remaining = remaining_limit()

        if remaining is not None:
            requested = min(requested, remaining)

        pmids = _pubmed_ids_for_query(query, requested, api_key=api_key, email=email)

        for pmid in pmids:
            if pmid in seen_pmids:
                continue

            seen_pmids.add(pmid)
            collected_pmids.append(pmid)

            if limit is not None and len(collected_pmids) >= limit:
                break

    if total_hits:
        collect_window(start_date, end_date)

    complete_retrieval = (
        single_day_truncations == 0
        and len(collected_pmids) >= total_hits
    )

    return collected_pmids, {
        "query": full_query,
        "total_publication_hits": total_hits,
        "retrieved_publications": len(collected_pmids),
        "requested_publication_limit": limit,
        "date_windows_searched": windows_searched,
        "single_day_truncations": single_day_truncations,
        "complete_retrieval": complete_retrieval,
    }


def _get_pubmed_date(article_data):
    article_dates = article_data.get("ArticleDate", []) or []
    if article_dates:
        item = article_dates[0]
        try:
            return date(int(item["Year"]), int(item["Month"]), int(item["Day"])).isoformat()
        except Exception:
            pass

    pub_date = article_data.get("Journal", {}).get("JournalIssue", {}).get("PubDate", {})
    year = str(pub_date.get("Year", "")).strip()
    month = str(pub_date.get("Month", "")).strip()
    day = str(pub_date.get("Day", "")).strip()

    if not year:
        medline_date = str(pub_date.get("MedlineDate", ""))
        match = re.search(r"\b(19|20)\d{2}\b", medline_date)
        year = match.group(0) if match else ""

    if not year:
        return None

    month_map = {
        "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
        "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12
    }
    month_number = int(month) if month.isdigit() else month_map.get(month[:3], 1)
    day_number = int(day) if day.isdigit() else 1

    try:
        return date(int(year), month_number, day_number).isoformat()
    except Exception:
        return f"{year}-01-01"


def _get_pubmed_ids(article):
    pmid = str(article.get("MedlineCitation", {}).get("PMID", "")).strip()
    doi = None

    for item in article.get("PubmedData", {}).get("ArticleIdList", []) or []:
        id_type = str(getattr(item, "attributes", {}).get("IdType", "")).lower()
        if id_type == "doi":
            doi = str(item).strip()
        elif id_type == "pubmed" and not pmid:
            pmid = str(item).strip()

    return pmid or None, doi


def _get_author_orcid(author_info):
    for identifier in author_info.get("Identifier", []) or []:
        source = str(getattr(identifier, "attributes", {}).get("Source", "")).lower()
        value = str(identifier).strip()
        if source == "orcid" or "orcid.org" in value:
            return clean_orcid(value)
    return None


def _fetch_pubmed_batch(pmids, api_key=None, email=None):
    if api_key:
        Entrez.api_key = api_key
    if email:
        Entrez.email = email

    handle = Entrez.efetch(db="pubmed", id=",".join(pmids), retmode="xml")
    records = Entrez.read(handle)
    handle.close()

    rows = []

    for article in records.get("PubmedArticle", []):
        citation = article.get("MedlineCitation", {})
        article_data = citation.get("Article", {})
        pmid, doi = _get_pubmed_ids(article)
        title = str(article_data.get("ArticleTitle", ""))
        journal_data = article_data.get("Journal", {})
        journal = str(journal_data.get("Title", "") or journal_data.get("ISOAbbreviation", "")).strip()
        publication_date = _get_pubmed_date(article_data)
        publication_year = pd.Timestamp(publication_date).year if publication_date else None

        for author_info in article_data.get("AuthorList", []) or []:
            last_name = str(author_info.get("LastName", "")).strip()
            fore_name = str(author_info.get("ForeName", "")).strip()
            initials = str(author_info.get("Initials", "")).strip()
            author_name = " ".join(part for part in [fore_name, initials, last_name] if part).strip()

            affiliations = [
                str(item.get("Affiliation", "")).strip()
                for item in author_info.get("AffiliationInfo", []) or []
                if str(item.get("Affiliation", "")).strip()
            ]
            affiliation_text = " | ".join(affiliations)
            university, region, country = match_hei(affiliation_text)

            if not university:
                continue

            rows.append({
                "Author": author_name,
                "ORCID": _get_author_orcid(author_info),
                "University": university,
                "Region": region,
                "Country": country,
                "Affiliation": affiliation_text,
                "Publication Date": publication_date,
                "Publication Year": publication_year,
                "Journal": journal,
                "Research Title": title,
                "Publication ID": pmid,
                "DOI": doi,
                "Citations": None,
                "Source Database": "PubMed",
                "Record URL": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None,
            })

    return rows


def search_pubmed(search_query, start_date, end_date, journals, max_publications=None, api_key=None, email=None):
    pmids, meta = _collect_pubmed_ids(
        search_query=search_query,
        start_date=start_date,
        end_date=end_date,
        journals=journals,
        max_publications=max_publications,
        api_key=api_key,
        email=email,
    )

    rows = []
    batch_size = 200

    for start in range(0, len(pmids), batch_size):
        batch = pmids[start:start + batch_size]
        rows.extend(_fetch_pubmed_batch(batch, api_key=api_key, email=email))
        time.sleep(0.11 if api_key else 0.34)

    df = pd.DataFrame(rows, columns=STANDARD_COLUMNS)

    # This is different from retrieved_publications:
    # it counts only fetched papers with at least one author matched to the UK HEI table.
    meta["publications_with_uk_hei_authors"] = (
        df["Publication ID"].nunique() if not df.empty else 0
    )
    meta["matched_author_publication_rows"] = len(df)

    return df, meta


def _openalex_headers(api_key):
    headers = {"User-Agent": "NARA-Author-Finder/1.0"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _request_openalex(endpoint, params, api_key=None, timeout=90):
    url = f"https://api.openalex.org/{endpoint.lstrip('/')}"
    response = requests.get(url, params=params, headers=_openalex_headers(api_key), timeout=timeout)
    response.raise_for_status()
    return response.json()


def _journal_aliases_for_openalex(journal_names):
    aliases = []
    for name in journal_names:
        aliases.extend(OPENALEX_JOURNAL_ALIASES.get(name, [name]))
        aliases.append(name)
    return list(dict.fromkeys(alias for alias in aliases if alias))


def resolve_openalex_source_ids(journal_names, api_key=None):
    if not journal_names:
        return [], []

    resolved = {}
    unresolved = []

    for original_name in journal_names:
        aliases = OPENALEX_JOURNAL_ALIASES.get(original_name, [original_name])
        found = None

        for query_name in aliases:
            payload = _request_openalex(
                "sources",
                {"search": query_name, "filter": "type:journal", "per_page": 25},
                api_key=api_key,
            )
            results = payload.get("results", [])
            target_names = {normalise_text(alias) for alias in [original_name, *aliases]}

            exact = [
                item for item in results
                if normalise_text(item.get("display_name")) in target_names
            ]

            if exact:
                found = exact[0]
                break

            for item in results:
                display = normalise_text(item.get("display_name"))
                if any(target and (target in display or display in target) for target in target_names):
                    found = item
                    break

            if found:
                break

        if found:
            resolved[original_name] = {
                "id": str(found.get("id", "")).rstrip("/").split("/")[-1],
                "display_name": found.get("display_name"),
            }
        else:
            unresolved.append(original_name)

    return list(resolved.values()), unresolved


def search_openalex(search_query, start_date, end_date, journals, max_publications=2000, api_key=None):
    source_records, unresolved_journals = resolve_openalex_source_ids(journals, api_key=api_key)
    source_ids = [item["id"] for item in source_records if item.get("id")]

    filters = [
        f"from_publication_date:{date_to_iso(start_date)}",
        f"to_publication_date:{date_to_iso(end_date)}",
        "authorships.institutions.country_code:GB",
        "type:article|review",
    ]

    if journals and source_ids:
        filters.append("primary_location.source.id:" + "|".join(source_ids))

    params = {
        "filter": ",".join(filters),
        "per_page": 100,
        "cursor": "*",
        "select": ",".join([
            "id", "doi", "display_name", "publication_date", "publication_year",
            "cited_by_count", "authorships", "primary_location"
        ]),
    }

    if search_query.strip():
        params["search"] = search_query.strip()

    rows = []
    retrieved_works = 0
    total_hits = None
    cursor = "*"

    while cursor and retrieved_works < int(max_publications):
        params["cursor"] = cursor
        payload = _request_openalex("works", params, api_key=api_key)

        if total_hits is None:
            total_hits = int(payload.get("meta", {}).get("count", 0))

        works = payload.get("results", [])
        if not works:
            break

        for work in works:
            if retrieved_works >= int(max_publications):
                break

            location = work.get("primary_location") or {}
            source = location.get("source") or {}
            journal = source.get("display_name")
            work_id = str(work.get("id", "")).rstrip("/").split("/")[-1] or None
            doi = str(work.get("doi", "") or "").replace("https://doi.org/", "") or None

            for authorship in work.get("authorships", []) or []:
                author = authorship.get("author") or {}
                institutions = authorship.get("institutions", []) or []

                matched = None
                for institution in institutions:
                    if institution.get("country_code") != "GB":
                        continue
                    university, region, country = match_hei(institution.get("display_name", ""))
                    if university:
                        matched = (university, region, country, institution.get("display_name", ""))
                        break

                if not matched:
                    continue

                university, region, country, affiliation = matched
                rows.append({
                    "Author": author.get("display_name"),
                    "ORCID": clean_orcid(author.get("orcid")),
                    "University": university,
                    "Region": region,
                    "Country": country,
                    "Affiliation": affiliation,
                    "Publication Date": work.get("publication_date"),
                    "Publication Year": work.get("publication_year"),
                    "Journal": journal,
                    "Research Title": work.get("display_name"),
                    "Publication ID": work_id,
                    "DOI": doi,
                    "Citations": work.get("cited_by_count", 0),
                    "Source Database": "OpenAlex",
                    "Record URL": work.get("id"),
                })

            retrieved_works += 1

        cursor = payload.get("meta", {}).get("next_cursor")

    df = pd.DataFrame(rows, columns=STANDARD_COLUMNS)

    meta = {
        "query": search_query.strip(),
        "total_publication_hits": total_hits or 0,
        "retrieved_publications": retrieved_works,
        "matched_author_publication_rows": len(df),
        "resolved_journals": [item["display_name"] for item in source_records],
        "unresolved_journals": unresolved_journals,
    }
    return df, meta


def apply_geography_filters(df, countries=None, regions=None, universities=None):
    if df.empty:
        return df.copy()

    filtered = df.copy()

    if countries:
        filtered = filtered[filtered["Country"].isin(countries)]
    if regions:
        filtered = filtered[filtered["Region"].isin(regions)]
    if universities:
        filtered = filtered[filtered["University"].isin(universities)]

    return filtered.reset_index(drop=True)


def build_member_summary(detailed_df):
    if detailed_df.empty:
        return pd.DataFrame(columns=[
            "Author", "ORCID", "University", "Region", "Country", "Publication Count",
            "Journal Count", "Total Citations", "Latest Publication Date", "Latest Journal",
            "Latest Research Title", "Latest Publication ID", "Latest DOI", "Source Database",
            "Identity Key Source", "Affiliation", "Record URL"
        ])

    df = detailed_df.copy()
    df["Publication Date"] = pd.to_datetime(df["Publication Date"], errors="coerce")

    name_key = df["Author"].fillna("").map(normalise_text)
    orcid_key = df["ORCID"].fillna("").map(clean_orcid)
    df["Identity Key"] = [
        f"orcid:{orcid}" if orcid else f"name:{name}"
        for orcid, name in zip(orcid_key, name_key)
    ]
    df["Identity Key Source"] = ["ORCID" if orcid else "Author name" for orcid in orcid_key]

    df = df.sort_values(["Publication Date", "Publication ID"], ascending=[False, False])

    counts = df.groupby("Identity Key").agg(
        **{
            "Publication Count": ("Publication ID", "nunique"),
            "Journal Count": ("Journal", "nunique"),
        }
    )

    if "Citations" in df.columns:
        citations = pd.to_numeric(df["Citations"], errors="coerce").fillna(0)
        df["_Citation Value"] = citations
        citation_totals = df.groupby("Identity Key")["_Citation Value"].sum().rename("Total Citations")
        counts = counts.join(citation_totals)

    latest = df.drop_duplicates("Identity Key", keep="first").set_index("Identity Key")
    latest = latest.join(counts)

    output = latest.reset_index(drop=True).rename(columns={
        "Publication Date": "Latest Publication Date",
        "Journal": "Latest Journal",
        "Research Title": "Latest Research Title",
        "Publication ID": "Latest Publication ID",
        "DOI": "Latest DOI",
    })

    columns = [
        "Author", "ORCID", "University", "Region", "Country", "Publication Count",
        "Journal Count", "Total Citations", "Latest Publication Date", "Latest Journal",
        "Latest Research Title", "Latest Publication ID", "Latest DOI", "Source Database",
        "Identity Key Source", "Affiliation", "Record URL"
    ]

    for column in columns:
        if column not in output.columns:
            output[column] = None

    return output[columns].sort_values(
        ["Publication Count", "Latest Publication Date", "Author"],
        ascending=[False, False, True]
    ).reset_index(drop=True)
