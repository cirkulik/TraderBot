//+------------------------------------------------------------------+
//|                                              GoldBotLevels.mq5   |
//|   Р РёСЃСѓРµС‚ Р·РѕРЅС‹ Рё СЃРёР»СЊРЅС‹Рµ С‚РѕС‡РєРё GoldBot РЅР° РіСЂР°С„РёРєРµ.                |
//|   Р”Р°РЅРЅС‹Рµ С‡РёС‚Р°РµС‚ РёР· Common\Files\GoldBot\levels.txt.              |
//|   РўРѕСЂРіРѕРІР»Рё РЅРµС‚: С‚РѕР»СЊРєРѕ РІРёР·СѓР°Р»СЊРЅС‹Рµ РѕР±СЉРµРєС‚С‹.                       |
//+------------------------------------------------------------------+
#property copyright "GoldBot"
#property version   "1.00"
#property indicator_chart_window
#property indicator_plots 0

input string InpFile          = "GoldBot\\levels.txt"; // С„Р°Р№Р» СЃ СѓСЂРѕРІРЅСЏРјРё
input int    InpTimerSeconds  = 2;                     // РїРµСЂРёРѕРґ РїСЂРѕРІРµСЂРєРё С„Р°Р№Р»Р°, СЃРµРє
input bool   InpShowZones     = true;                  // РїРѕРєР°Р·С‹РІР°С‚СЊ Р·РѕРЅС‹
input bool   InpShowPoints    = true;                  // РїРѕРєР°Р·С‹РІР°С‚СЊ С‚РѕС‡РєРё
input int    InpFontSize      = 8;                     // СЂР°Р·РјРµСЂ РїРѕРґРїРёСЃРё
input int    InpFutureBars    = 6;                     // РїСЂРѕРґР»РёС‚СЊ РїРѕР»РѕСЃС‹ РІРїСЂР°РІРѕ, Р±Р°СЂРѕРІ
input bool   InpDebug         = false;                 // РїРёСЃР°С‚СЊ РѕС‚Р»Р°РґРєСѓ РІ Р¶СѓСЂРЅР°Р»

#define PREFIX "GBMAP_"

string   g_zone_names[];
string   g_label_names[];
double   g_label_prices[];

datetime g_stamp = 0;
int      g_count = 0;

//+------------------------------------------------------------------+
int OnInit()
  {
   EventSetTimer((int)MathMax(1, InpTimerSeconds));
   Refresh(true);
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   EventKillTimer();
   ObjectsDeleteAll(0, PREFIX);
   ChartRedraw();
  }

//+------------------------------------------------------------------+
void OnTimer()
  {
   Refresh(false);
  }

//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
  {
   if(id == CHARTEVENT_CHART_CHANGE)
      UpdateTimes();
  }

//+------------------------------------------------------------------+
int OnCalculate(const int rates_total,
                const int prev_calculated,
                const datetime &time[],
                const double &open[],
                const double &high[],
                const double &low[],
                const double &close[],
                const long &tick_volume[],
                const long &volume[],
                const int &spread[])
  {
   return(rates_total);
  }

//+------------------------------------------------------------------+
bool IsStrong(string strength)
  {
   return(strength == "strong" || strength == "strongest");
  }

//+------------------------------------------------------------------+
color SideColor(string side, string strength)
  {
   bool buy = (side == "buy");
   if(IsStrong(strength))
      return(buy ? clrLime : clrRed);
   if(strength == "medium")
      return(buy ? clrGreen : clrFireBrick);
   return(buy ? clrDarkGreen : clrMaroon);
  }

//+------------------------------------------------------------------+
color ZoneFill(string side, string strength)
  {
   bool buy = (side == "buy");
   if(IsStrong(strength))
      return(buy ? C'18,52,30' : C'60,22,22');
   if(strength == "medium")
      return(buy ? C'14,40,24' : C'48,18,18');
   return(buy ? C'10,28,18' : C'36,14,14');
  }

//+------------------------------------------------------------------+
ENUM_LINE_STYLE PointStyle(string strength)
  {
   if(IsStrong(strength))
      return(STYLE_SOLID);
   if(strength == "medium")
      return(STYLE_DASH);
   return(STYLE_DOT);
  }

//+------------------------------------------------------------------+
void AddName(string &names[], string name)
  {
   int n = ArraySize(names);
   ArrayResize(names, n + 1);
   names[n] = name;
  }

//+------------------------------------------------------------------+
void AddPrice(double &values[], double value)
  {
   int n = ArraySize(values);
   ArrayResize(values, n + 1);
   values[n] = value;
  }

//+------------------------------------------------------------------+
void VisibleRange(datetime &t1, datetime &t2)
  {
   int total = Bars(_Symbol, _Period);
   if(total <= 0)
     {
      t1 = TimeCurrent() - 3600;
      t2 = TimeCurrent();
      return;
     }

   int first = (int)ChartGetInteger(0, CHART_FIRST_VISIBLE_BAR);
   int visible = (int)ChartGetInteger(0, CHART_VISIBLE_BARS);
   if(first < 0)
      first = 0;
   if(first >= total)
      first = total - 1;
   if(visible < 1)
      visible = 1;

   int right = first - visible + 1;
   if(right < 0)
      right = 0;

   t1 = iTime(_Symbol, _Period, first);
   t2 = iTime(_Symbol, _Period, 0) + InpFutureBars * PeriodSeconds();
   if(t1 <= 0)
      t1 = t2 - 3600;
   if(t1 >= t2)
      t1 = t2 - 3600;
  }

//+------------------------------------------------------------------+
void DrawLabel(string name, datetime when, double price, string side,
               string strength, int touches, ENUM_ANCHOR_POINT anchor)
  {
   for(int i = 0; i < ArraySize(g_label_prices); i++)
     {
      if(MathAbs(g_label_prices[i] - price) < 0.30)
         return;
     }

   if(!ObjectCreate(0, name, OBJ_TEXT, 0, when, price))
      return;

   ObjectSetString(0, name, OBJPROP_TEXT,
                   DoubleToString(price, _Digits) + " (" + IntegerToString(touches) + ")");
   ObjectSetInteger(0, name, OBJPROP_COLOR, SideColor(side, strength));
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, InpFontSize);
   ObjectSetString(0, name, OBJPROP_FONT, "Arial");
   ObjectSetInteger(0, name, OBJPROP_ANCHOR, anchor);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_SELECTED, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   ObjectSetInteger(0, name, OBJPROP_BACK, false);

   AddName(g_label_names, name);
   AddPrice(g_label_prices, price);
  }

//+------------------------------------------------------------------+
void DrawZone(int index, datetime t1, datetime t2, string side, double price,
              double half, string strength, int touches)
  {
   string base = PREFIX + "Z" + IntegerToString(index);
   string fill = base + "_f";
   string line = base + "_l";
   double top = price + half;
   double bottom = price - half;

   if(ObjectCreate(0, fill, OBJ_RECTANGLE, 0, t1, top, t2, bottom))
     {
      ObjectSetInteger(0, fill, OBJPROP_COLOR, ZoneFill(side, strength));
      ObjectSetInteger(0, fill, OBJPROP_FILL, true);
      ObjectSetInteger(0, fill, OBJPROP_BACK, true);
      ObjectSetInteger(0, fill, OBJPROP_WIDTH, 1);
      ObjectSetInteger(0, fill, OBJPROP_STYLE, STYLE_SOLID);
      ObjectSetInteger(0, fill, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, fill, OBJPROP_SELECTED, false);
      ObjectSetInteger(0, fill, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, fill, OBJPROP_ZORDER, 0);
      AddName(g_zone_names, fill);
     }

   if(ObjectCreate(0, line, OBJ_RECTANGLE, 0, t1, top, t2, bottom))
     {
      ObjectSetInteger(0, line, OBJPROP_COLOR, SideColor(side, strength));
      ObjectSetInteger(0, line, OBJPROP_FILL, false);
      ObjectSetInteger(0, line, OBJPROP_BACK, false);
      ObjectSetInteger(0, line, OBJPROP_WIDTH, IsStrong(strength) ? 2 : 1);
      ObjectSetInteger(0, line, OBJPROP_STYLE, STYLE_SOLID);
      ObjectSetInteger(0, line, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, line, OBJPROP_SELECTED, false);
      ObjectSetInteger(0, line, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, line, OBJPROP_ZORDER, 1);
      AddName(g_zone_names, line);
     }

   DrawLabel(base + "_t", iTime(_Symbol, _Period, 0), price, side, strength, touches, ANCHOR_RIGHT);
  }

//+------------------------------------------------------------------+
void DrawPoint(int index, datetime when, string side, double price,
               string strength, int touches)
  {
   string line = PREFIX + "P" + IntegerToString(index) + "_l";

   if(ObjectCreate(0, line, OBJ_HLINE, 0, 0, price))
     {
      ObjectSetInteger(0, line, OBJPROP_COLOR, SideColor(side, strength));
      ObjectSetInteger(0, line, OBJPROP_STYLE, PointStyle(strength));
      ObjectSetInteger(0, line, OBJPROP_WIDTH, IsStrong(strength) ? 2 : 1);
      ObjectSetInteger(0, line, OBJPROP_BACK, false);
      ObjectSetInteger(0, line, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, line, OBJPROP_SELECTED, false);
      ObjectSetInteger(0, line, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, line, OBJPROP_ZORDER, 2);
     }

   DrawLabel(line + "_t", when, price, side, strength, touches, ANCHOR_RIGHT);
  }

//+------------------------------------------------------------------+
void UpdateTimes()
  {
   if(ArraySize(g_zone_names) == 0 && ArraySize(g_label_names) == 0)
      return;

   datetime t1, t2;
   VisibleRange(t1, t2);

   for(int i = 0; i < ArraySize(g_zone_names); i++)
     {
      ObjectSetInteger(0, g_zone_names[i], OBJPROP_TIME, 0, t1);
      ObjectSetInteger(0, g_zone_names[i], OBJPROP_TIME, 1, t2);
     }

   for(int i = 0; i < ArraySize(g_label_names); i++)
      ObjectSetInteger(0, g_label_names[i], OBJPROP_TIME, 0, iTime(_Symbol, _Period, 0));

   ChartRedraw();
  }

//+------------------------------------------------------------------+
void Refresh(bool force)
  {
   int handle = FileOpen(InpFile, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(handle == INVALID_HANDLE)
     {
      if(InpDebug)
         Print("GBMAP: РЅРµ РѕС‚РєСЂС‹С‚ ", InpFile, ", РѕС€РёР±РєР° ", GetLastError());
      return;
     }

   string types[];
   string sides[];
   double prices[];
   double halves[];
   string strengths[];
   int touches[];
   int count = 0;
   string sym = "";
   datetime stamp = 0;
   ushort sep = StringGetCharacter("|", 0);

   while(!FileIsEnding(handle))
     {
      string line = FileReadString(handle);
      if(StringLen(line) == 0)
         continue;
      if(StringGetCharacter(line, 0) == '#')
         continue;

      string parts[];
      int n = StringSplit(line, sep, parts);
      if(n < 2)
         continue;

      if(parts[0] == "S")
        {
         sym = parts[1];
         if(n >= 4)
            stamp = (datetime)StringToInteger(parts[3]);
         continue;
        }

      if(parts[0] != "Z" && parts[0] != "P")
         continue;
      if(n < 6)
         continue;

      ArrayResize(types, count + 1);
      ArrayResize(sides, count + 1);
      ArrayResize(prices, count + 1);
      ArrayResize(halves, count + 1);
      ArrayResize(strengths, count + 1);
      ArrayResize(touches, count + 1);

      types[count] = parts[0];
      sides[count] = parts[1];
      prices[count] = StringToDouble(parts[2]);
      halves[count] = StringToDouble(parts[3]);
      strengths[count] = parts[4];
      touches[count] = (int)StringToInteger(parts[5]);
      count++;
     }

   FileClose(handle);

   if(count == 0)
      return;
   if(sym != "" && sym != _Symbol)
      return;

   if(!force && stamp == g_stamp && count == g_count)
     {
      UpdateTimes();
      return;
     }

   g_stamp = stamp;
   g_count = count;

   ObjectsDeleteAll(0, PREFIX);
   ArrayResize(g_zone_names, 0);
   ArrayResize(g_label_names, 0);
   ArrayResize(g_label_prices, 0);

   datetime t1, t2;
   VisibleRange(t1, t2);

   int zi = 0;
   int pi = 0;
   for(int i = 0; i < count; i++)
     {
      if(types[i] == "Z")
        {
         if(!InpShowZones)
            continue;
         DrawZone(zi, t1, t2, sides[i], prices[i], halves[i], strengths[i], touches[i]);
         zi++;
        }
      else
        {
         if(!InpShowPoints)
            continue;
         DrawPoint(pi, iTime(_Symbol, _Period, 0), sides[i], prices[i], strengths[i], touches[i]);
         pi++;
        }
     }

   ChartRedraw();
  }
//+------------------------------------------------------------------+

