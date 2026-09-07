import os
from datetime import date

import pandas as pd
import streamlit as st

from config import COUNTRY_ORDER, NARA_JOURNALS, REGION_ORDER, UK_HEIS
from services import (
    apply_geography_filters,
    build_member_summary,
    search_openalex,
    search_pubmed,
)


st.set_page_config(
    page_title="NARA UK Researcher Finder",
    page_icon="🔎",
    layout="wide",
)


def get_secret(name):
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return os.getenv(name, "")


@st.cache_data(ttl=3600, show_spinner=False)
def cached_pubmed_search(search_query, start_date, end_date, journals, max_publications, _api_key, _email):
    return search_pubmed(
        search_query=search_query,
        start_date=start_date,
        end_date=end_date,
        journals=list(journals),
        max_publications=max_publications,
        api_key=_api_key or None,
        email=_email or None,
    )


@st.cache_data(ttl=3600, show_spinner=False)
def cached_openalex_search(search_query, start_date, end_date, journals, max_publications, _api_key):
    return search_openalex(
        search_query=search_query,
        start_date=start_date,
        end_date=end_date,
        journals=list(journals),
        max_publications=max_publications,
        api_key=_api_key or None,
    )


def csv_bytes(df):
    return df.to_csv(index=False).encode("utf-8-sig")


def available_heis(countries, regions):
    rows = []
    for item in UK_HEIS:
        if countries and item["Country"] not in countries:
            continue
        if regions and item["Region"] not in regions:
            continue
        rows.append(item["University"])
    return sorted(set(rows))


st.title("NARA UK Researcher Finder")
st.caption(
    "Find UK HEI researchers from PubMed or OpenAlex by topic, journal, date range, region and institution."
)

with st.sidebar:
    st.header("Search")

    database = st.radio("Database", ["PubMed", "OpenAlex"], horizontal=True)

    search_query = st.text_area(
        "Search query",
        value='alcohol OR addiction OR "substance use"',
        height=90,
        help="Simple terms work. You can also use database-specific Boolean syntax.",
    )

    date_col1, date_col2 = st.columns(2)
    with date_col1:
        start_date = st.date_input("From", value=date(2020, 1, 1), min_value=date(1990, 1, 1), max_value=date(2030, 12, 31))
    with date_col2:
        end_date = st.date_input("To", value=date.today(), min_value=date(1990, 1, 1), max_value=date(2030, 12, 31))

    st.subheader("Journals")
    journal_scope = st.selectbox("Journal scope", ["NARA journal set", "All journals", "Custom journals"])

    if journal_scope == "NARA journal set":
        selected_journals = st.multiselect(
            "Selected journals",
            NARA_JOURNALS,
            default=NARA_JOURNALS,
        )
    elif journal_scope == "Custom journals":
        custom_journals = st.text_area(
            "One journal per line",
            placeholder="Addiction\nDrug and Alcohol Dependence",
            height=120,
        )
        selected_journals = [line.strip() for line in custom_journals.splitlines() if line.strip()]
    else:
        selected_journals = []

    st.subheader("UK geography")
    selected_countries = st.multiselect("Countries", COUNTRY_ORDER, default=COUNTRY_ORDER)
    selected_regions = st.multiselect("Regions", REGION_ORDER, default=REGION_ORDER)
    hei_options = available_heis(selected_countries, selected_regions)
    selected_heis = st.multiselect("HEIs", hei_options, default=[])

    if database == "PubMed":
        retrieval_mode = st.radio(
            "Publication retrieval",
            ["All matching publications", "Limit publications"],
            help="All matching automatically splits large PubMed searches into smaller date windows."
        )

        if retrieval_mode == "All matching publications":
            max_publications = None
        else:
            max_publications = st.select_slider(
                "Maximum publications to retrieve",
                options=[100, 250, 500, 1000, 2000, 5000, 10000, 20000, 50000],
                value=10000,
            )
    else:
        max_publications = st.select_slider(
            "Maximum publications to retrieve",
            options=[100, 250, 500, 1000, 2000, 5000, 10000, 20000],
            value=2000,
        )

    st.subheader("API access")
    if database == "PubMed":
        stored_key = get_secret("NCBI_API_KEY")
        stored_email = get_secret("NCBI_EMAIL")

        ncbi_key_input = st.text_input(
            "NCBI API key",
            value="",
            type="password",
            placeholder="Configured in secrets" if stored_key else "Optional but recommended",
        )
        ncbi_email_input = st.text_input(
            "NCBI email",
            value="",
            placeholder=stored_email or "you@example.com",
        )

        active_ncbi_key = ncbi_key_input or stored_key
        active_ncbi_email = ncbi_email_input or stored_email
        active_openalex_key = ""
    else:
        stored_key = get_secret("OPENALEX_API_KEY")
        openalex_key_input = st.text_input(
            "OpenAlex API key",
            value="",
            type="password",
            placeholder="Configured in secrets" if stored_key else "Enter your OpenAlex key",
        )

        active_openalex_key = openalex_key_input or stored_key
        active_ncbi_key = ""
        active_ncbi_email = ""

    run_search = st.button("Run search", type="primary", use_container_width=True)


if run_search:
    if start_date > end_date:
        st.error("The start date must be before the end date.")
        st.stop()

    if not search_query.strip() and not selected_journals:
        st.error("Enter a search query or select at least one journal.")
        st.stop()

    if database == "OpenAlex" and not active_openalex_key:
        st.error("Add an OpenAlex API key in the sidebar or Streamlit secrets.")
        st.stop()

    with st.status(f"Searching {database}…", expanded=True) as status:
        st.write("Retrieving publications and author affiliations.")

        try:
            if database == "PubMed":
                detailed_df, meta = cached_pubmed_search(
                    search_query,
                    start_date.isoformat(),
                    end_date.isoformat(),
                    tuple(selected_journals),
                    max_publications,
                    active_ncbi_key,
                    active_ncbi_email,
                )
            else:
                detailed_df, meta = cached_openalex_search(
                    search_query,
                    start_date.isoformat(),
                    end_date.isoformat(),
                    tuple(selected_journals),
                    max_publications,
                    active_openalex_key,
                )
        except Exception as exc:
            status.update(label="Search failed", state="error")
            st.exception(exc)
            st.stop()

        st.write("Applying country, region and HEI filters.")
        detailed_df = apply_geography_filters(
            detailed_df,
            countries=selected_countries,
            regions=selected_regions,
            universities=selected_heis,
        )
        member_df = build_member_summary(detailed_df)

        st.session_state["detailed_df"] = detailed_df
        st.session_state["member_df"] = member_df
        st.session_state["search_meta"] = meta
        st.session_state["database_used"] = database

        status.update(label="Search complete", state="complete")


member_df = st.session_state.get("member_df", pd.DataFrame())
detailed_df = st.session_state.get("detailed_df", pd.DataFrame())
meta = st.session_state.get("search_meta", {})
database_used = st.session_state.get("database_used", database)

if member_df.empty and detailed_df.empty:
    st.info("Configure the search in the sidebar and click **Run search**.")
    st.stop()


if database_used == "PubMed":
    metric1, metric2, metric3, metric4, metric5 = st.columns(5)
    metric1.metric("Potential members", f"{len(member_df):,}")
    metric2.metric("PubMed hits", f"{meta.get('total_publication_hits', 0):,}")
    metric3.metric("Retrieved", f"{meta.get('retrieved_publications', 0):,}")
    metric4.metric("Matched publications", f"{detailed_df['Publication ID'].nunique():,}" if not detailed_df.empty else "0")
    metric5.metric("UK HEIs", f"{member_df['University'].nunique():,}" if not member_df.empty else "0")
else:
    metric1, metric2, metric3, metric4 = st.columns(4)
    metric1.metric("Potential members", f"{len(member_df):,}")
    metric2.metric("UK HEIs", f"{member_df['University'].nunique():,}" if not member_df.empty else "0")
    metric3.metric("Matched publications", f"{detailed_df['Publication ID'].nunique():,}" if not detailed_df.empty else "0")
    metric4.metric("Regions", f"{member_df['Region'].nunique():,}" if not member_df.empty else "0")

if meta:
    total_hits = meta.get("total_publication_hits", 0)
    retrieved = meta.get("retrieved_publications", 0)

    if database_used == "PubMed":
        matched_before_filters = meta.get("publications_with_uk_hei_authors", 0)
        limit = meta.get("requested_publication_limit")
        complete = meta.get("complete_retrieval", False)
        truncations = meta.get("single_day_truncations", 0)

        if complete:
            st.success(
                f"PubMed found {total_hits:,} matching publications and all {retrieved:,} were retrieved. "
                f"{matched_before_filters:,} of them had at least one author matched to the UK HEI table before your region/HEI filters."
            )
        elif limit and retrieved >= limit:
            st.warning(
                f"PubMed found {total_hits:,} matching publications. This run retrieved {retrieved:,} because "
                f"the selected publication limit was {limit:,}. {matched_before_filters:,} retrieved publications "
                f"had at least one author matched to the UK HEI table before your region/HEI filters."
            )
        elif truncations:
            st.warning(
                f"PubMed found {total_hits:,} matching publications and retrieved {retrieved:,}. "
                f"{truncations} single-day search window(s) still exceeded PubMed's per-query retrieval ceiling."
            )
        else:
            st.info(
                f"PubMed found {total_hits:,} matching publications and retrieved {retrieved:,}. "
                f"{matched_before_filters:,} had at least one UK-HEI-matched author before your geography filters."
            )
    elif total_hits and retrieved < total_hits:
        st.warning(
            f"OpenAlex reports {total_hits:,} publication hits. "
            f"This run retrieved {retrieved:,} because of the selected publication limit."
        )

    unresolved = meta.get("unresolved_journals", [])
    if unresolved:
        with st.expander("OpenAlex journals that could not be resolved"):
            st.write(unresolved)


members_tab, details_tab, coverage_tab, query_tab = st.tabs(
    ["Potential members", "Publication records", "Coverage", "Search details"]
)

with members_tab:
    st.subheader("Potential members")

    filter_col1, filter_col2, filter_col3 = st.columns(3)
    with filter_col1:
        min_publications = st.number_input("Minimum publications", min_value=1, value=1, step=1)
    with filter_col2:
        result_regions = st.multiselect(
            "Filter displayed regions",
            sorted(member_df["Region"].dropna().unique().tolist()),
            default=[],
        )
    with filter_col3:
        result_heis = st.multiselect(
            "Filter displayed HEIs",
            sorted(member_df["University"].dropna().unique().tolist()),
            default=[],
        )

    displayed_members = member_df[member_df["Publication Count"] >= min_publications].copy()
    if result_regions:
        displayed_members = displayed_members[displayed_members["Region"].isin(result_regions)]
    if result_heis:
        displayed_members = displayed_members[displayed_members["University"].isin(result_heis)]

    st.dataframe(
        displayed_members,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Record URL": st.column_config.LinkColumn("Latest record"),
            "ORCID": st.column_config.TextColumn("ORCID"),
        },
    )

    st.download_button(
        "Download potential members CSV",
        csv_bytes(displayed_members),
        file_name=f"nara_potential_members_{database_used.lower()}.csv",
        mime="text/csv",
        use_container_width=True,
    )


with details_tab:
    st.subheader("Author-publication records")
    st.dataframe(
        detailed_df,
        use_container_width=True,
        hide_index=True,
        column_config={"Record URL": st.column_config.LinkColumn("Record")},
    )

    st.download_button(
        "Download publication-level CSV",
        csv_bytes(detailed_df),
        file_name=f"nara_author_publications_{database_used.lower()}.csv",
        mime="text/csv",
        use_container_width=True,
    )


with coverage_tab:
    if member_df.empty:
        st.info("No matched UK HEI researchers were found.")
    else:
        st.subheader("Regional coverage")
        region_summary = (
            member_df.groupby(["Country", "Region"])
            .agg(Authors=("Author", "nunique"), HEIs=("University", "nunique"))
            .reset_index()
        )
        st.dataframe(region_summary, use_container_width=True, hide_index=True)

        st.subheader("Top HEIs")
        hei_summary = (
            member_df.groupby(["Region", "University"])
            .agg(Authors=("Author", "nunique"), Publications=("Publication Count", "sum"))
            .reset_index()
            .sort_values(["Authors", "Publications"], ascending=False)
        )
        st.dataframe(hei_summary.head(30), use_container_width=True, hide_index=True)

        st.subheader("Top journals")
        journal_summary = (
            detailed_df.groupby("Journal")
            .agg(Authors=("Author", "nunique"), Publications=("Publication ID", "nunique"))
            .reset_index()
            .sort_values(["Authors", "Publications"], ascending=False)
        )
        st.dataframe(journal_summary.head(30), use_container_width=True, hide_index=True)


with query_tab:
    st.subheader("Search details")
    st.json(
        {
            "database": database_used,
            "query": meta.get("query"),
            "reported_publication_hits": meta.get("total_publication_hits"),
            "retrieved_publications": meta.get("retrieved_publications"),
            "publications_with_uk_hei_authors_before_geography_filters": meta.get("publications_with_uk_hei_authors"),
            "matched_publications_after_geography_filters": detailed_df["Publication ID"].nunique() if not detailed_df.empty else 0,
            "matched_author_publication_rows_after_geography_filters": len(detailed_df),
            "requested_publication_limit": meta.get("requested_publication_limit"),
            "date_windows_searched": meta.get("date_windows_searched"),
            "complete_retrieval": meta.get("complete_retrieval"),
        }
    )

    if database_used == "OpenAlex" and meta.get("resolved_journals"):
        st.write("Resolved OpenAlex journals")
        st.write(meta["resolved_journals"])
