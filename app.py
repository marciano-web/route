import streamlit as st
import pandas as pd
import folium
from folium.plugins import AntPath
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from fpdf import FPDF
import base64
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import time
import os
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
import io
import PyPDF2
from PyPDF2 import PdfReader, PdfWriter
from reportlab.lib.pagesizes import landscape, A4
from io import BytesIO

# NOVA IMPORTAÇÃO PARA GEOCODIFICAÇÃO
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
import requests

def detectar_e_converter_coordenadas(df):
    """
    Detecta se as coordenadas estão em microdegrees e converte para graus decimais.
    """
    if 'latitude' in df.columns and 'longitude' in df.columns:
        # Verifica se os valores são muito grandes (indicando microdegrees)
        lat_sample = df['latitude'].iloc[0] if len(df) > 0 else 0
        lon_sample = df['longitude'].iloc[0] if len(df) > 0 else 0
        
        # Se os valores absolutos são maiores que 180, provavelmente estão em microdegrees
        if abs(lat_sample) > 180 or abs(lon_sample) > 180:
            st.warning("⚠️ Coordenadas detectadas em formato microdegrees. Convertendo automaticamente para graus decimais...")
            df['latitude'] = df['latitude'] / 1000000
            df['longitude'] = df['longitude'] / 1000000
            st.success("✅ Coordenadas convertidas com sucesso!")
            
            # Mostra exemplo da conversão
            st.info(f"Exemplo de conversão:\n"
                   f"Latitude: {lat_sample} → {lat_sample/1000000:.6f}\n"
                   f"Longitude: {lon_sample} → {lon_sample/1000000:.6f}")
    
    return df

def obter_endereco_por_coordenadas(latitude, longitude, timeout=10):
    """
    Converte coordenadas de latitude e longitude em endereço usando geocodificação reversa.
    """
    try:
        # Inicializa o geocodificador Nominatim (OpenStreetMap)
        geolocator = Nominatim(user_agent="temperatura_umidade_app")
        
        # Faz a geocodificação reversa
        location = geolocator.reverse(f"{latitude}, {longitude}", timeout=timeout, language='pt')
        
        if location:
            # Extrai informações do endereço
            address = location.address
            
            # Tenta extrair informações mais específicas
            raw_data = location.raw.get('address', {})
            
            # Constrói um endereço mais limpo
            endereco_parts = []
            
            # Adiciona rua/avenida
            if 'road' in raw_data:
                endereco_parts.append(raw_data['road'])
            elif 'pedestrian' in raw_data:
                endereco_parts.append(raw_data['pedestrian'])
            
            # Adiciona número se disponível
            if 'house_number' in raw_data:
                endereco_parts[-1] += f", {raw_data['house_number']}"
            
            # Adiciona bairro
            if 'suburb' in raw_data:
                endereco_parts.append(raw_data['suburb'])
            elif 'neighbourhood' in raw_data:
                endereco_parts.append(raw_data['neighbourhood'])
            
            # Adiciona cidade
            if 'city' in raw_data:
                endereco_parts.append(raw_data['city'])
            elif 'town' in raw_data:
                endereco_parts.append(raw_data['town'])
            elif 'village' in raw_data:
                endereco_parts.append(raw_data['village'])
            
            # Adiciona estado
            if 'state' in raw_data:
                endereco_parts.append(raw_data['state'])
            
            # Adiciona país
            if 'country' in raw_data:
                endereco_parts.append(raw_data['country'])
            
            if endereco_parts:
                return " - ".join(endereco_parts)
            else:
                return address
        else:
            return "Endereço não encontrado"
            
    except GeocoderTimedOut:
        return "Timeout na busca do endereço"
    except GeocoderServiceError:
        return "Erro no serviço de geocodificação"
    except Exception as e:
        return f"Erro: {str(e)}"

def adicionar_enderecos_ao_dataframe(df, progress_bar=None):
    """
    Adiciona uma coluna de endereços ao DataFrame baseada nas coordenadas.
    """
    if 'latitude' not in df.columns or 'longitude' not in df.columns:
        st.error("Colunas de latitude e longitude não encontradas!")
        return df
    
    enderecos = []
    total_rows = len(df)
    
    # Cache para evitar consultas repetidas
    cache_enderecos = {}
    
    for i, row in df.iterrows():
        lat = row['latitude']
        lon = row['longitude']
        
        # Cria uma chave para o cache (arredonda para 6 casas decimais)
        cache_key = f"{lat:.6f},{lon:.6f}"
        
        if cache_key in cache_enderecos:
            endereco = cache_enderecos[cache_key]
        else:
            endereco = obter_endereco_por_coordenadas(lat, lon)
            cache_enderecos[cache_key] = endereco
            
            # Pequena pausa para não sobrecarregar o serviço
            time.sleep(0.1)
        
        enderecos.append(endereco)
        
        # Atualiza barra de progresso se fornecida
        if progress_bar:
            progress_bar.progress((i + 1) / total_rows)
    
    # Adiciona a coluna de endereços
    df['endereco'] = enderecos
    return df

def carregar_dados(uploaded_file):
    """Carrega e processa os dados do arquivo Excel."""
    try:
        df = pd.read_excel(uploaded_file, sheet_name="Sheet1")
        df = df.dropna(axis=1, how='all')
        df["Date Time"] = pd.to_datetime(df["Date Time"], errors='coerce')
        if "Temperatura (°C)" not in df.columns:
            st.error("Erro: A coluna 'Temperatura (°C)' não foi encontrada no arquivo Excel.")
            return None
        df = df.dropna(subset=["Date Time", "latitude", "longitude", "Temperatura (°C)", "Hora"])
        
        # NOVA FUNCIONALIDADE: Detecta e converte coordenadas automaticamente
        df = detectar_e_converter_coordenadas(df)
        
        df["longitude"] = df["longitude"].astype(float)
        df["latitude"] = df["latitude"].astype(float)
        return df
    except Exception as e:
        st.error(f"Erro ao carregar dados: {e}")
        return None

from folium import Map, Marker, Icon, FitBounds

def criar_mapa_com_enderecos(df):
    """
    Cria um mapa com marcadores que incluem endereços nos popups.
    """
    lat_col = next((col for col in df.columns if 'lat' in col.lower()), None)
    lon_col = next((col for col in df.columns if 'lon' in col.lower() or 'lng' in col.lower()), None)

    if not lat_col or not lon_col:
        raise KeyError("Colunas de latitude e longitude não foram encontradas no DataFrame.")

    lat_lon = list(zip(df[lat_col], df[lon_col]))

    m = folium.Map(location=lat_lon[0] if lat_lon else [0, 0], zoom_start=10, tiles="OpenStreetMap")
    if lat_lon:
        folium.FitBounds(lat_lon).add_to(m)
        folium.PolyLine(lat_lon, color="blue", weight=2.5, opacity=1).add_to(m)

    marker_locations = []

    for i, (lat, lon) in enumerate(lat_lon):
        # Obtém o endereço se disponível
        endereco = df.iloc[i].get('endereco', 'Endereço não disponível')
        
        # Cria popup com informações detalhadas
        popup_text = f"""
        <b>Ponto {i + 1}</b><br>
        <b>Coordenadas:</b> {lat:.6f}, {lon:.6f}<br>
        <b>Endereço:</b> {endereco}<br>
        <b>Data/Hora:</b> {df.iloc[i]['Date Time']}<br>
        <b>Temperatura:</b> {df.iloc[i]['Temperatura (°C)']}°C<br>
        <b>Umidade:</b> {df.iloc[i]['Umidade (%UR)']}%
        """
        
        folium.Marker(
            location=(lat, lon),
            popup=folium.Popup(popup_text, max_width=300),
            icon=folium.DivIcon(html=f'<div style="font-size: 10pt; color: white; background-color: blue; border-radius: 50%; width: 20px; height: 20px; text-align: center; line-height: 20px;">{i + 1}</div>')
        ).add_to(m)
        
        marker_locations.append([str(i + 1), f"{lat:.6f}, {lon:.6f}", endereco])

    map_file = "mapa.html"
    m.save(map_file)
    return map_file, marker_locations

def criar_mapa(df):
    """Função original para criar mapa sem endereços."""
    lat_col = next((col for col in df.columns if 'lat' in col.lower()), None)
    lon_col = next((col for col in df.columns if 'lon' in col.lower() or 'lng' in col.lower()), None)

    if not lat_col or not lon_col:
        raise KeyError("Colunas de latitude e longitude não foram encontradas no DataFrame.")

    lat_lon = list(zip(df[lat_col], df[lon_col]))

    m = folium.Map(location=lat_lon[0] if lat_lon else [0, 0], zoom_start=10, tiles="OpenStreetMap")
    if lat_lon:
        folium.FitBounds(lat_lon).add_to(m)
        folium.PolyLine(lat_lon, color="blue", weight=2.5, opacity=1).add_to(m)

    marker_locations = []

    for i, (lat, lon) in enumerate(lat_lon):
        folium.Marker(
            location=(lat, lon),
            icon=folium.DivIcon(html=f'<div style="font-size: 10pt">{i + 1}</div>')
        ).add_to(m)
        marker_locations.append([str(i + 1), f"{lat}, {lon}"])

    map_file = "mapa.html"
    m.save(map_file)
    return map_file, marker_locations

def calcular_resumo_temperatura(df, li_temp, ls_temp):
    """Calcula o resumo de temperatura por hora."""
    resumo_temp = df.groupby("Hora").agg(Temperatura_Mínima=("Temperatura (°C)", "min"), Temperatura_Média=("Temperatura (°C)", "mean"), Temperatura_Máxima=("Temperatura (°C)", "max")).reset_index()
    def calcular_percentuais_temp(grupo):
        total = len(grupo)
        abaixo = (grupo["Temperatura (°C)"] < li_temp).sum() / total * 100 if total > 0 else 0
        dentro = ((grupo["Temperatura (°C)"] >= li_temp) & (grupo["Temperatura (°C)"] <= ls_temp)).sum() / total * 100 if total > 0 else 0
        acima = (grupo["Temperatura (°C)"] > ls_temp).sum() / total * 100 if total > 0 else 0
        return pd.Series({"% Abaixo da especificação": abaixo, "% Dentro da especificação": dentro, "% Acima da especificação": acima})
    percentuais_temp = df.groupby("Hora").apply(calcular_percentuais_temp).reset_index()
    resumo_temp = resumo_temp.merge(percentuais_temp, on="Hora", how="left").drop(columns=["Hora"])
    resumo_temp.insert(0, "Intervalo", [f"{i+1}ª Hora" for i in range(len(resumo_temp))])
    resumo_temp.fillna(0, inplace=True)
    resumo_temp_display = resumo_temp.copy()
    for coluna in resumo_temp_display.columns:
        if resumo_temp_display[coluna].dtype == 'float64' or resumo_temp_display[coluna].dtype == 'int64':
            resumo_temp_display[coluna] = resumo_temp_display[coluna].map('{:.2f}'.format)
    resumo_temp_pdf = resumo_temp.copy()
    resumo_temp_numeric = resumo_temp.copy()
    for coluna in resumo_temp_pdf.columns:
        if resumo_temp_pdf[coluna].dtype == 'float64' or resumo_temp_pdf[coluna].dtype == 'int64':
            resumo_temp_pdf[coluna] = resumo_temp_pdf[coluna].map('{:.2f}'.format)
    return resumo_temp_display, resumo_temp_pdf, resumo_temp_numeric

def calcular_resumo_umidade(df, li_umid, ls_umid):
    """Calcula o resumo de umidade por hora."""
    resumo_umid = df.groupby("Hora").agg(Umidade_Mínima=("Umidade (%UR)", "min"), Umidade_Média=("Umidade (%UR)", "mean"), Umidade_Máxima=("Umidade (%UR)", "max")).reset_index()
    def calcular_percentuais_umid(grupo):
        total = len(grupo)
        abaixo = (grupo["Umidade (%UR)"] < li_umid).sum() / total * 100 if total > 0 else 0
        dentro = ((grupo["Umidade (%UR)"] >= li_umid) & (grupo["Umidade (%UR)"] <= ls_umid)).sum() / total * 100 if total > 0 else 0
        acima = (grupo["Umidade (%UR)"] > ls_umid).sum() / total * 100 if total > 0 else 0
        return pd.Series({"% Abaixo da especificação": abaixo, "% Dentro da especificação": dentro, "% Acima da especificação": acima})
    percentuais_umid = df.groupby("Hora").apply(calcular_percentuais_umid).reset_index()
    resumo_umid = resumo_umid.merge(percentuais_umid, on="Hora", how="left").drop(columns=["Hora"])
    resumo_umid.insert(0, "Intervalo", [f"{i+1}ª Hora" for i in range(len(resumo_umid))])
    resumo_umid.fillna(0, inplace=True)
    resumo_umid_display = resumo_umid.copy()
    for coluna in resumo_umid_display.columns:
        if resumo_umid_display[coluna].dtype == 'float64' or resumo_umid_display[coluna].dtype == 'int64':
            resumo_umid_display[coluna] = resumo_umid_display[coluna].map('{:.2f}'.format)
    resumo_umid_pdf = resumo_umid.copy()
    resumo_umid_numeric = resumo_umid.copy()
    for coluna in resumo_umid_pdf.columns:
        if resumo_umid_pdf[coluna].dtype == 'float64' or resumo_umid_pdf[coluna].dtype == 'int64':
            resumo_umid_pdf[coluna] = resumo_umid_pdf[coluna].map('{:.2f}'.format)
    return resumo_umid_display, resumo_umid_pdf, resumo_umid_numeric

def criar_graficos(df, resumo_temp, resumo_umid, li_temp, ls_temp, li_umid, ls_umid):
    """Cria gráficos de temperatura e umidade ao longo do tempo."""
    
    # Gráfico de Temperaturas por Hora
    fig_temp, ax_temp = plt.subplots(figsize=(12, 6))
    
    # Converte os dados de temperatura para numérico (se necessário)
    resumo_temp["Temperatura_Mínima"] = pd.to_numeric(resumo_temp["Temperatura_Mínima"], errors='coerce')
    resumo_temp["Temperatura_Média"] = pd.to_numeric(resumo_temp["Temperatura_Média"], errors='coerce')
    resumo_temp["Temperatura_Máxima"] = pd.to_numeric(resumo_temp["Temperatura_Máxima"], errors='coerce')

    # Define a escala do eixo Y para temperatura com base nos dados numéricos
    min_temp = min(resumo_temp["Temperatura_Mínima"].min(), li_temp) - 1
    max_temp = max(resumo_temp["Temperatura_Máxima"].max(), ls_temp) + 1
    ax_temp.set_yticks(range(int(min_temp), int(max_temp) + 1, 2))  # Ajuste o intervalo conforme necessário

    # Plota o gráfico de temperatura
    ax_temp.plot(resumo_temp["Intervalo"], resumo_temp["Temperatura_Mínima"], marker="o", label="Temp. Mínima", color="blue")
    ax_temp.plot(resumo_temp["Intervalo"], resumo_temp["Temperatura_Média"], marker="o", label="Temp. Média", color="orange")
    ax_temp.plot(resumo_temp["Intervalo"], resumo_temp["Temperatura_Máxima"], marker="o", label="Temp. Máxima", color="green")
    ax_temp.axhline(y=li_temp, color="red", linestyle="--", label=f"LI - Especificação ({li_temp:.2f}°C)")
    ax_temp.axhline(y=ls_temp, color="green", linestyle="--", label=f"LS - Especificação ({ls_temp:.2f}°C)")
    ax_temp.tick_params(axis='x', labelrotation=45, labelsize=7)  
    if len(resumo_temp) > 20:
        plt.xticks(range(0, len(resumo_temp), 2))
    
    # REMOVIDO: Rótulos de dados para temperatura (conforme solicitado)
    
    ax_temp.set_xlabel("Intervalo")
    ax_temp.set_ylabel("Temperatura (°C)")
    ax_temp.legend()
    ax_temp.grid(True)
    plt.tight_layout()
    
    # Gráfico de Umidade Relativa por Hora
    fig_umid, ax_umid = plt.subplots(figsize=(12, 6))
    
    # Converte os dados de umidade para numérico (se necessário)
    resumo_umid["Umidade_Mínima"] = pd.to_numeric(resumo_umid["Umidade_Mínima"], errors='coerce')
    resumo_umid["Umidade_Média"] = pd.to_numeric(resumo_umid["Umidade_Média"], errors='coerce')
    resumo_umid["Umidade_Máxima"] = pd.to_numeric(resumo_umid["Umidade_Máxima"], errors='coerce')

    # Define a escala do eixo Y para umidade com base nos dados numéricos
    min_umid = min(resumo_umid["Umidade_Mínima"].min(), li_umid) - 1
    max_umid = max(resumo_umid["Umidade_Máxima"].max(), ls_umid) + 1
    ax_umid.set_yticks(range(0, 101, 10))  # Escala de 0 a 100 com espaçamento de 10

    # Plota o gráfico de umidade
    ax_umid.plot(resumo_umid["Intervalo"], resumo_umid["Umidade_Mínima"], marker="o", label="Umid. Mínima", color="blue")
    ax_umid.plot(resumo_umid["Intervalo"], resumo_umid["Umidade_Média"], marker="o", label="Umid. Média", color="orange")
    ax_umid.plot(resumo_umid["Intervalo"], resumo_umid["Umidade_Máxima"], marker="o", label="Umid. Máxima", color="green")
    ax_umid.axhline(y=li_umid, color="red", linestyle="--", label=f"LI - Especificação ({li_umid:.2f}%)")
    ax_umid.axhline(y=ls_umid, color="green", linestyle="--", label=f"LS - Especificação ({ls_umid:.2f}%)")
    ax_umid.tick_params(axis='x', labelrotation=45, labelsize=7)  
    if len(resumo_umid) > 20:
        plt.xticks(range(0, len(resumo_umid), 2))
    
    # REMOVIDO: Rótulos de dados para umidade (conforme solicitado)
    
    ax_umid.set_xlabel("Intervalo")
    ax_umid.set_ylabel("Umidade Relativa (%)")
    ax_umid.legend()
    ax_umid.grid(True)
    plt.tight_layout()
    
    # Gráfico de Temperatura e Luz ao longo do tempo
    fig_temp_luz, ax1 = plt.subplots(figsize=(12, 6))
    ax1.plot(df["Date Time"], df["Temperatura (°C)"], marker="o", label="Temperatura (°C)", color="blue")
    ax1.set_xlabel("Data e Hora")
    ax1.set_ylabel("Temperatura (°C)", color="blue")
    ax1.tick_params(axis="y", labelcolor="blue")
    ax1.axhline(y=li_temp, color="red", linestyle="--", label=f"LI - Especificação ({li_temp:.2f}°C)")
    ax1.axhline(y=ls_temp, color="green", linestyle="--", label=f"LS - Especificação ({ls_temp:.2f}°C)")
    ax2 = ax1.twinx()
    ax2.plot(df["Date Time"], df["Luz (lx)"], marker="s", label="Luz (lx)", color="orange")
    ax2.set_ylabel("Luz (lx)", color="orange")
    ax2.tick_params(axis="y", labelcolor="orange")
    ax2.tick_params(axis='x', labelrotation=45, labelsize=7)
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left")
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%d-%m %H:%M"))
    ax1.xaxis.set_major_locator(mdates.HourLocator(interval=2))
    ax1.tick_params(axis='x', labelrotation=45, labelsize=7)
    plt.xticks(rotation=45, fontsize=8)
    plt.tight_layout()
    
    return fig_temp, fig_umid, fig_temp_luz

def mostrar_tabela_resumo_temperatura(df, li_temp, ls_temp):
    col_temp = "Temperatura (°C)"
    total = df[col_temp].count()

    resumo_data = {
        "ºC Mínima": [df[col_temp].min()],
        "ºC Média": [df[col_temp].mean()],
        "ºC Máxima": [df[col_temp].max()],
        "%Abaixo da especificação": [((df[col_temp] < li_temp).sum() / total) * 100],
        "%Dentro da especificação": [(((df[col_temp] >= li_temp) & (df[col_temp] <= ls_temp)).sum() / total) * 100],
        "%Acima da especificação": [((df[col_temp] > ls_temp).sum() / total) * 100]
    }

    st.subheader("Tabela de resumo de dados de temperatura")
    resumo_df = pd.DataFrame(resumo_data)
    st.dataframe(
        resumo_df.style
            .format("{:.2f}")
            .set_properties(**{"text-align": "center"})
            .set_table_styles([{"selector": "th", "props": [("text-align", "center")]}])
    )
    return resumo_df

def criar_grafico_umidade_luz(df, li_umid, ls_umid):
    """Cria um gráfico de umidade e luz ao longo do tempo."""
    fig_umid_luz, ax1 = plt.subplots(figsize=(12, 6))
    ax1.plot(df["Date Time"], df["Umidade (%UR)"], marker="o", label="Umidade (%UR)", color="blue")
    ax1.set_xlabel("Data e Hora")
    ax1.set_ylabel("Umidade (%UR)", color="blue")
    ax1.tick_params(axis="y", labelcolor="blue")
    ax1.axhline(y=li_umid, color="red", linestyle="--", label=f"LI - Especificação ({li_umid:.2f}%)")
    ax1.axhline(y=ls_umid, color="green", linestyle="--", label=f"LS - Especificação ({ls_umid:.2f}%)")
    ax2 = ax1.twinx()
    ax2.plot(df["Date Time"], df["Luz (lx)"], marker="s", label="Luz (lx)", color="orange")
    ax2.set_ylabel("Luz (lx)", color="orange")
    ax2.tick_params(axis="y", labelcolor="orange")
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left")
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%d-%m %H:%M"))
    ax1.xaxis.set_major_locator(mdates.HourLocator(interval=2))
    ax1.tick_params(axis='x', labelrotation=45, labelsize=7)
    plt.xticks(rotation=45, fontsize=8)
    plt.tight_layout()
    return fig_umid_luz

def mostrar_tabela_resumo_umidade(df, li_umid, ls_umid):
    col_umid = "Umidade (%UR)"
    total = df[col_umid].count()

    resumo_data = {
        "%UR Mínima": [df[col_umid].min()],
        "%UR Média": [df[col_umid].mean()],
        "%UR Máxima": [df[col_umid].max()],
        "%Abaixo da especificação": [((df[col_umid] < li_umid).sum() / total) * 100],
        "%Dentro da especificação": [(((df[col_umid] >= li_umid) & (df[col_umid] <= ls_umid)).sum() / total) * 100],
        "%Acima da especificação": [((df[col_umid] > ls_umid).sum() / total) * 100]
    }

    st.subheader("Tabela de resumo de dados de Umidade Relativa")
    resumo_df = pd.DataFrame(resumo_data)
    st.dataframe(
        resumo_df.style
            .format("{:.2f}")
            .set_properties(**{"text-align": "center"})
            .set_table_styles([{"selector": "th", "props": [("text-align", "center")]}])
    )
    return resumo_df

def capturar_mapa(map_file):
    """Captura o mapa Folium como imagem."""
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1200x800")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    driver.get(f"file:///{os.path.abspath(map_file)}")
    time.sleep(5)
    map_image = "mapa_interativo.png"
    driver.save_screenshot(map_image)
    driver.quit()
    return map_image

def adicionar_resumo_temp_pdf(pdf, resumo_df, max_page_width):
    col_widths = [260 / len(resumo_df.columns)] * len(resumo_df.columns)
    pdf.set_font("Arial", "B", 10)
    pdf.ln(5)
    pdf.cell(0, 10, "Tabela de resumo de dados de temperatura", ln=True, align="C")

    pdf.set_font("Arial", "B", 8)
    for i, col in enumerate(resumo_df.columns):
        pdf.cell(col_widths[i], 8, col, border=1, align="C")
    pdf.ln()

    pdf.set_font("Arial", "", 8)
    for i, val in enumerate(resumo_df.iloc[0]):
        val_str = f"{val:.2f}" if isinstance(val, (float, int)) else str(val)
        pdf.cell(col_widths[i], 8, val_str, border=1, align="C")
    pdf.ln(10)

def adicionar_resumo_umid_pdf(pdf, resumo_df, max_page_width):
    col_widths = [260 / len(resumo_df.columns)] * len(resumo_df.columns)
    pdf.set_font("Arial", "B", 10)
    pdf.ln(5)
    pdf.cell(0, 10, "Tabela de resumo de dados de Umidade Relativa", ln=True, align="C")

    pdf.set_font("Arial", "B", 8)
    for i, col in enumerate(resumo_df.columns):
        pdf.cell(col_widths[i], 8, col, border=1, align="C")
    pdf.ln()

    pdf.set_font("Arial", "", 8)
    for i, val in enumerate(resumo_df.iloc[0]):
        val_str = f"{val:.2f}" if isinstance(val, (float, int)) else str(val)
        pdf.cell(col_widths[i], 8, val_str, border=1, align="C")
    pdf.ln(10)

def criar_pdf(df, resumo_temp_pdf, resumo_temp_numeric, resumo_umid_pdf, resumo_umid_numeric,
              marker_locations, map_image, fig_temp, fig_umid, fig_temp_luz, fig_umid_luz,
              observacoes, li_temp, ls_temp, li_umid, ls_umid, resumo_temp_tabela, resumo_umid_tabela):
    from fpdf import FPDF
    import matplotlib.pyplot as plt
    pdf = FPDF(orientation='L', unit='mm', format='A4')
    pdf.set_margins(left=10, top=10, right=10)  # 1cm = 10mm
    pdf.set_auto_page_break(auto=True, margin=10)
    max_page_width = 277  # 297mm (A4 horizontal) - 2x10mm margem

    # Página 1 – Capa
    pdf.add_page(orientation='L')
    pdf.set_font("Arial", "B", 28)
    pdf.set_xy(10, (pdf.h - 20) / 2 - 20)
    pdf.multi_cell(0, 20, "Dados brutos do teste de Distribuição térmica em Rota", align="C")

    if observacoes:
        pdf.ln(10)
        pdf.set_font("Arial", "", 12)
        pdf.multi_cell(0, 10, "Observações: " + observacoes, align="C")

    # Página 2 – Mapa
    pdf.add_page(orientation='L')
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, "Mapa do trajeto da rota", ln=True, align="C")
    pdf.image(map_image, x=10, y=20, w=260)

    pdf.add_page()
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, "Gráfico de Temperaturas por Hora", ln=True, align="C")
    fig_temp.savefig("temp_graph.png")
    pdf.image("temp_graph.png", x=10, y=20, w=260)

    pdf.add_page()
    draw_table(pdf, resumo_temp_pdf.columns.tolist(), resumo_temp_pdf.values.tolist(),
               "Resumo de Temperaturas por Hora", max_page_width,
               li_temp=li_temp, ls_temp=ls_temp,
               row_height=8, allow_header_break=True,
               is_summary_table=True, numeric_data=resumo_temp_numeric.values.tolist())

    pdf.add_page()
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, "Gráfico de Umidade Relativa por Hora", ln=True, align="C")
    fig_umid.savefig("umid_graph.png")
    pdf.image("umid_graph.png", x=10, y=20, w=260)

    pdf.add_page()
    draw_table(pdf, resumo_umid_pdf.columns.tolist(), resumo_umid_pdf.values.tolist(),
               "Resumo de Umidade Relativa por Hora", max_page_width,
               li_umid=li_umid, ls_umid=ls_umid,
               row_height=8, allow_header_break=True,
               is_summary_table=True, numeric_data=resumo_umid_numeric.values.tolist())

    pdf.add_page(orientation='L')
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, "Gráfico de Temperatura e Luz ao Longo do Tempo", ln=True, align="C")

    # Salvar gráfico reduzido
    temp_luz_path = "grafico_temp_luz.png"
    fig_temp_luz.set_size_inches(10, 3.5)  # reduzir tamanho físico do gráfico
    fig_temp_luz.tight_layout()
    fig_temp_luz.savefig(temp_luz_path, bbox_inches='tight')

    # Inserir imagem e deixar espaço
    pdf.image(temp_luz_path, x=10, y=20, w=260, h=90)

    # Espaço depois do gráfico
    pdf.set_y(120)

    # Inserir tabela na mesma página
    adicionar_resumo_temp_pdf(pdf, resumo_temp_tabela, max_page_width)

    pdf.add_page(orientation='L')
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, "Gráfico de Umidade relativa e Luz ao Longo do Tempo", ln=True, align="C")

    umid_luz_path = "grafico_umid_luz.png"
    fig_umid_luz.set_size_inches(10, 3.5)
    fig_umid_luz.tight_layout()
    fig_umid_luz.savefig(umid_luz_path, bbox_inches='tight')

    pdf.image(umid_luz_path, x=10, y=20, w=260, h=90)
    pdf.set_y(120)
    adicionar_resumo_umid_pdf(pdf, resumo_umid_tabela, max_page_width)

    pdf.add_page()
    df_pdf = df.drop(columns=["Hora"])
    draw_table(pdf, df_pdf.columns.tolist(), df_pdf.values.tolist(), "",
               max_page_width, li_temp=li_temp, ls_temp=ls_temp,
               li_umid=li_umid, ls_umid=ls_umid, row_height=8)

    pdf.output("relatorio_temp.pdf")
    return pdf.page_no()

def add_page_numbers(input_pdf, output_pdf):
    existing_pdf = PdfReader(input_pdf)
    output = PdfWriter()

    for i, page in enumerate(existing_pdf.pages):
        packet = BytesIO()
        can = canvas.Canvas(packet, pagesize=landscape(A4))

        # Centralizar horizontalmente com base na largura da página
        page_width = landscape(A4)[0]
        text = f"{i + 1} de {len(existing_pdf.pages)}"
        text_width = can.stringWidth(text, "Helvetica", 10)
        x = (page_width - text_width) / 2  # Centralizado
        y = 15  # Distância do rodapé

        can.setFont("Helvetica", 10)
        can.drawString(x, y, text)
        can.save()
        packet.seek(0)

        overlay = PdfReader(packet)
        page.merge_page(overlay.pages[0])
        output.add_page(page)

    with open(output_pdf, "wb") as f:
        output.write(f)

def formatar_numero_pdf(valor):
    """
    Formata números para exibição no PDF com exatamente 2 casas decimais.
    """
    try:
        if isinstance(valor, (int, float)):
            return f"{float(valor):.2f}"
        else:
            # Tenta converter string para float
            num = float(str(valor).replace(",", "."))
            return f"{num:.2f}"
    except (ValueError, TypeError):
        return str(valor)

def calculate_column_widths_with_address(pdf, data, headers):
    """
    Calcula a largura das colunas considerando endereços longos.
    """
    col_widths = []
    max_page_width = 277  # Largura máxima da página
    
    for col_idx in range(len(headers)):
        header_name = headers[col_idx].strip().lower()
        
        if header_name == "endereco":
            # Coluna de endereço recebe 40% da largura da página
            col_widths.append(max_page_width * 0.40)
        else:
            # Outras colunas recebem largura baseada no conteúdo
            col_content = [str(headers[col_idx])] + [str(row[col_idx]) for row in data if len(row) > col_idx]
            max_width = max(pdf.get_string_width(str(item)) for item in col_content)
            col_widths.append(max_width + 8)
    
    # Ajusta proporcionalmente se necessário
    total_width = sum(col_widths)
    if total_width > max_page_width:
        ratio = max_page_width / total_width
        col_widths = [w * ratio for w in col_widths]
    
    return [round(w, 2) for w in col_widths]

def check_if_text_fits_in_width(pdf, text, width):
    """
    Verifica se o texto cabe na largura especificada.
    """
    text_width = pdf.get_string_width(str(text))
    return text_width <= width

def draw_table(pdf, headers, data, title, max_page_width,
               li_temp=None, ls_temp=None, li_umid=None, ls_umid=None,
               row_height=8, allow_header_break=True,
               is_summary_table=False, numeric_data=None):
    
    # Calcula larguras das colunas
    col_widths = calculate_column_widths_with_address(pdf, data, headers)

    if title:
        pdf.set_font("Arial", "B", 14)
        pdf.cell(0, 10, title, ln=True, align="C")
        pdf.ln(1)

    # Cabeçalho
    pdf.set_font("Arial", "B", 8)
    pdf.set_fill_color(200, 220, 255)
    for i, header in enumerate(headers):
        pdf.cell(col_widths[i], row_height, str(header), border=1, fill=True, align="C")
    pdf.ln()
    pdf.set_font("Arial", "", 8)

    # Dados
    for row_idx, row in enumerate(data):
        # CORREÇÃO: Verifica se algum endereço na linha precisa quebrar
        needs_line_break = False
        for col_idx, cell in enumerate(row):
            header_name = headers[col_idx].strip().lower()
            if header_name == "endereco":
                cell_value = str(cell)
                # Codificação segura para latin-1
                try:
                    cell_value.encode('latin-1')
                except UnicodeEncodeError:
                    cell_value = cell_value.encode('latin-1', 'replace').decode('latin-1')
                
                # Verifica se o texto não cabe na largura da coluna
                if not check_if_text_fits_in_width(pdf, cell_value, col_widths[col_idx]):
                    needs_line_break = True
                    break
        
        # CORREÇÃO: Define altura baseada na necessidade de quebra - ALTURA REDUZIDA
        current_row_height = row_height * 1.8 if needs_line_break else row_height
        
        # Verifica se precisa de nova página
        if pdf.get_y() + current_row_height > pdf.h - pdf.b_margin - 15:
            if allow_header_break:
                pdf.add_page()
                pdf.set_font("Arial", "B", 8)
                pdf.set_fill_color(200, 220, 255)
                for i, header in enumerate(headers):
                    pdf.cell(col_widths[i], row_height, str(header), border=1, fill=True, align="C")
                pdf.ln()
                pdf.set_font("Arial", "", 8)

        # Salva posição inicial da linha
        start_y = pdf.get_y()
        
        # Processa cada célula da linha
        for col_idx, cell in enumerate(row):
            align = "C" if is_numeric(cell) else "L"
            header_name = headers[col_idx].strip().lower()

            # Configuração de cores para temperatura e umidade
            if header_name in ["temperatura (°c)", "umidade (%ur)"] or (
                is_summary_table and header_name in [
                    "temperatura_mínima", "temperatura_média", "temperatura_máxima",
                    "umidade_mínima", "umidade_média", "umidade_máxima"
                ]
            ):
                try:
                    if is_summary_table and numeric_data is not None:
                        value = float(numeric_data[row_idx][col_idx])
                    else:
                        value = float(cell) if isinstance(cell, (int, float)) else float(str(cell).replace(",", "."))

                    if header_name.startswith("temperatura"):
                        li = li_temp
                        ls = ls_temp
                    elif header_name.startswith("umidade"):
                        li = li_umid
                        ls = ls_umid
                    else:
                        li = ls = None

                    if li is not None and ls is not None:
                        if value > ls:
                            pdf.set_text_color(255, 0, 0)
                        elif value < li:
                            pdf.set_text_color(0, 0, 255)
                        else:
                            pdf.set_text_color(0, 0, 0)
                    else:
                        pdf.set_text_color(0, 0, 0)
                except (ValueError, TypeError):
                    pdf.set_text_color(0, 0, 0)
            else:
                pdf.set_text_color(0, 0, 0)

            # Formatação do conteúdo
            if is_numeric(cell):
                cell_value = formatar_numero_pdf(cell)
            else:
                cell_value = str(cell)
                
            # Codificação segura para latin-1
            try:
                cell_value.encode('latin-1')
            except UnicodeEncodeError:
                cell_value = cell_value.encode('latin-1', 'replace').decode('latin-1')

            # CORREÇÃO: Tratamento especial para endereços
            if header_name == "endereco":
                x = pdf.get_x()
                y = pdf.get_y()
                
                # CORREÇÃO: Só usa multi_cell se o texto não couber
                if not check_if_text_fits_in_width(pdf, cell_value, col_widths[col_idx]):
                    # CORREÇÃO: Usa altura menor para multi_cell (4px ao invés de 5px)
                    pdf.multi_cell(col_widths[col_idx], 4, cell_value, border=1, align="L")
                else:
                    # Usa cell normal para textos que cabem
                    pdf.cell(col_widths[col_idx], current_row_height, cell_value, border=1, align="L")
                
                # Reposiciona para a próxima coluna
                pdf.set_xy(x + col_widths[col_idx], y)
            else:
                # CORREÇÃO: Todas as outras células usam a altura calculada
                pdf.cell(col_widths[col_idx], current_row_height, cell_value, border=1, align=align)

        # CORREÇÃO: Move para a próxima linha usando a altura calculada
        pdf.set_y(start_y + current_row_height)
        
    pdf.set_text_color(0, 0, 0)

def calculate_column_widths(pdf, data, headers):
    """Calcula a largura das colunas com base no conteúdo."""
    col_widths = []
    for col_idx in range(len(headers)):
        col_content = [str(headers[col_idx])] + [str(row[col_idx]) for row in data if len(row) > col_idx]
        max_width = max(pdf.get_string_width(str(item)) for item in col_content)
        col_widths.append(max_width + 8)  # Margem extra para não cortar texto
    return col_widths

def adjust_column_widths(col_widths, max_page_width):
    """Ajusta as larguras das colunas proporcionalmente para caber na largura da página."""
    total_width = sum(col_widths)
    if total_width > max_page_width:
        ratio = max_page_width / total_width
        col_widths = [min(w * ratio, 50) for w in col_widths]  # de 65 para 50
    return [round(w, 2) for w in col_widths]

def is_numeric(value):
    """Verifica se um valor pode ser interpretado como numérico (float)."""
    try:
        float(str(value).replace(",", "."))
        return True
    except (ValueError, TypeError):
        return False

# Interface Streamlit
st.set_page_config(page_title="Gerador de Mapas e Análises com Geocodificação", layout="wide")
st.title("🗺️ Gerador de Mapas e Análises com Geocodificação")

st.markdown("""
### 🆕 Funcionalidades:
- ✅ **Conversão automática** de coordenadas microdegrees para graus decimais
- ✅ **Geocodificação reversa** - converte coordenadas em endereços
- ✅ **Popups informativos** no mapa com endereços completos
- ✅ **Tabela de localizações** com coordenadas e endereços
- ✅ **Todos os gráficos** e análises da versão original
- ✅ **Relatório PDF completo** com endereços incluídos
- ✅ **Gráficos limpos** sem rótulos de dados desnecessários
- ✅ **Formatação corrigida** no PDF (2 casas decimais)
- ✅ **Altura reduzida** para melhor aproveitamento do espaço
""")

st.markdown("---")

# Tipo de análise
analysis_type = st.selectbox("Selecione o tipo de análise:", ["Temperatura e Umidade"])

# Limites de Temperatura
st.subheader("🎯 Definir limites de especificação")
col1, col2 = st.columns(2)
li_temp = col1.number_input("LI - Temperatura (°C)", value=15.0, step=0.1)
ls_temp = col2.number_input("LS - Temperatura (°C)", value=30.0, step=0.1)

# Limites de Umidade (se aplicável)
if analysis_type == "Temperatura e Umidade":
    col3, col4 = st.columns(2)
    li_umid = col3.number_input("LI - Umidade (%)", value=0.0, step=0.1)
    ls_umid = col4.number_input("LS - Umidade (%)", value=100.0, step=0.1)

# Observações
observacoes = st.text_area("📝 Observações", placeholder="Insira observações sobre a análise...")

# Upload do arquivo Excel
uploaded_file = st.file_uploader("📁 Arraste e solte o arquivo Excel aqui", type=["xlsx"])

if uploaded_file is not None:
    df = carregar_dados(uploaded_file)
    if df is not None:
        # Opção para adicionar geocodificação
        st.subheader("🌍 Geocodificação")
        add_geocoding = st.checkbox("Adicionar endereços baseados nas coordenadas", value=True)
        
        if add_geocoding:
            if 'endereco' not in df.columns:
                st.info("🔍 Buscando endereços para as coordenadas...")
                progress_bar = st.progress(0)
                df = adicionar_enderecos_ao_dataframe(df, progress_bar)
                progress_bar.empty()
                st.success("✅ Endereços adicionados com sucesso!")
            
            # Cria mapa com endereços
            map_file, marker_locations = criar_mapa_com_enderecos(df)
        else:
            # Usa função original sem endereços
            map_file, marker_locations = criar_mapa(df)

        # Exibe o mapa
        st.subheader("🗺️ Mapa da Rota")
        with open(map_file, 'r', encoding='utf-8') as f:
            map_html = f.read()
        st.components.v1.html(map_html, height=600)

        # Mostra tabela de localizações
        st.subheader("📍 Localizações")
        if add_geocoding and len(marker_locations[0]) > 2:
            coords_df = pd.DataFrame(marker_locations, columns=["Ponto", "Coordenadas", "Endereço"])
        else:
            coords_df = pd.DataFrame(marker_locations, columns=["Ponto", "Coordenadas"])
        st.dataframe(coords_df, use_container_width=True)

        # Cálculos e análises
        resumo_temp_display, resumo_temp_pdf, resumo_temp_numeric = calcular_resumo_temperatura(df, li_temp, ls_temp)
        resumo_umid_display, resumo_umid_pdf, resumo_umid_numeric = calcular_resumo_umidade(df, li_umid, ls_umid)

        st.subheader("🌡️ Resumo de Temperaturas por Hora")
        st.dataframe(resumo_temp_display)

        st.subheader("💧 Resumo de Umidade Relativa por Hora")
        st.dataframe(resumo_umid_display)

        # Mostra dados completos incluindo endereços se disponível
        df_display = df.drop(columns=["Hora"])
        st.subheader("📊 Dados Completos do Arquivo")
        st.dataframe(df_display)

        # Criar gráficos
        fig_temp, fig_umid, fig_temp_luz = criar_graficos(df, resumo_temp_display, resumo_umid_display, li_temp, ls_temp, li_umid, ls_umid)
        fig_umid_luz = criar_grafico_umidade_luz(df, li_umid, ls_umid)

        st.subheader("📈 Gráfico de Temperaturas por Hora")
        st.pyplot(fig_temp)

        st.subheader("📈 Gráfico de Umidade Relativa por Hora")
        st.pyplot(fig_umid)

        st.subheader("📈 Gráfico de Temperatura e Luz ao Longo do Tempo")
        st.pyplot(fig_temp_luz)
        # Mostrar tabela de resumo de temperatura abaixo do gráfico
        resumo_temp_tabela = mostrar_tabela_resumo_temperatura(df, li_temp, ls_temp)

        st.subheader("📈 Gráfico de Umidade relativa e Luz ao Longo do Tempo")
        st.pyplot(fig_umid_luz)
        # Mostrar tabela de resumo de umidade relativa abaixo do gráfico
        resumo_umid_tabela = mostrar_tabela_resumo_umidade(df, li_umid, ls_umid)

        # Botão para gerar relatório PDF
        if st.button("📄 Gerar Relatório PDF"):
            with st.spinner("Gerando relatório PDF..."):
                map_image = capturar_mapa(map_file)
                criar_pdf(
                    df, resumo_temp_pdf, resumo_temp_numeric,
                    resumo_umid_pdf, resumo_umid_numeric,
                    marker_locations, map_image,
                    fig_temp, fig_umid, fig_temp_luz, fig_umid_luz,
                    observacoes, li_temp, ls_temp, li_umid, ls_umid,
                    resumo_temp_tabela, resumo_umid_tabela
                )

                add_page_numbers("relatorio_temp.pdf", "relatorio.pdf")

                with open("relatorio.pdf", "rb") as f:
                    base64_pdf = base64.b64encode(f.read()).decode("utf-8")
                href = f'<a href="data:file/pdf;base64,{base64_pdf}" download="relatorio.pdf">📥 Clique aqui para baixar o relatório PDF</a>'
                st.markdown(href, unsafe_allow_html=True)
                st.success("✅ Relatório PDF gerado com sucesso!")

else:
    st.info("👆 Por favor, faça upload de um arquivo Excel para começar a análise.")
    
    # Instruções para o usuário
    st.markdown("""
    ### 📝 Instruções de Uso
    
    1. **Upload do Arquivo**: Faça upload de um arquivo Excel (.xlsx) com os dados de temperatura e umidade
    2. **Formato das Coordenadas**: A aplicação detecta automaticamente:
       - **Graus decimais** (ex: -22.943178, -43.384319) ✅
       - **Microdegrees** (ex: -22943178, -43384319) ✅ *Conversão automática*
    3. **Geocodificação**: Marque a opção para converter coordenadas em endereços
    4. **Colunas Necessárias**: 
       - `Date Time`: Data e hora das medições
       - `Temperatura (°C)`: Valores de temperatura
       - `Umidade (%UR)`: Valores de umidade
       - `latitude` e `longitude`: Coordenadas geográficas
       - `Hora`: Identificador da hora de coleta
    
    ### 🎯 Melhorias Implementadas
    
    - ✅ **Gráficos mais limpos** sem rótulos de dados desnecessários
    - ✅ **Formatação corrigida** no PDF (sempre 2 casas decimais)
    - ✅ **Geocodificação completa** com endereços nos popups e tabelas
    - ✅ **Relatório PDF profissional** com todas as análises
    - ✅ **Altura reduzida** - melhor aproveitamento do espaço na tabela
    - ✅ **Quebra inteligente** - só quebra quando endereço excede largura da coluna
    """)

# Limpar arquivos temporários
for file in ["mapa_interativo.html", "mapa.html", "grafico_temp_luz.png", "grafico_umid_luz.png", "mapa_interativo.png", "umid_graph.png", "umid_light_graph.png", "temp_graph.png", "temp_light_graph.png", "relatorio_temp.pdf", "relatorio.pdf"]:
    if os.path.exists(file):
        os.remove(file)

