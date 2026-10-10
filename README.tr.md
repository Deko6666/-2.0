# Üniversite İş-Öğrenim (Work-Study) Maaş Otomasyon Yönetim Sistemi

Blokzincir tabanlı üniversite iş-öğrenim (work-study) yönetim sistemi: iş ilanı yayınlama, öğrenci başvurusu, öğretmen onayı, çalışma saati kaydı (punch-in) ve maaşın otomatik hesaplanması süreçlerinin tamamı blokzincir üzerinde gerçekleşir. Backend iş akışlarının düzenlenmesi ve verilerin kalıcılığından sorumludur; frontend çok rollü bir işlem arayüzü sunar.

- **Blokzincir**: Geth Dev Chain (Chain ID `887766` / `0xD8BD6`, RPC `http://127.0.0.1:8545`)
- **Akıllı kontrat**: `contracts/WorkStudy.sol` (Solidity 0.8.20)
- **Backend**: FastAPI + web3.py + MySQL (SQLAlchemy), port `8000`
- **Frontend**: Tek dosyalık `frontend/index.html` (ethers.js 6.7 + vanilla JS), doğrudan FastAPI tarafından sunulur

---

## Sistem Mimarisi

```
┌─────────────────┐     HTTP /api      ┌──────────────────┐     JSON-RPC     ┌──────────────┐
│  Tarayıcı        │ ─────────────────▶ │  FastAPI Backend │ ───────────────▶ │  Geth Düğümü │
│  Önyüzü          │ ◀───────────────── │   main.py        │ ◀─────────────── │  WorkStudy   │
│  (ethers.js)     │   MetaMask işlemi  │   + MySQL        │  Zincir okuma/   │  Kontratı    │
│                  │                    │                  │  oto. ödeme      │              │
└─────────────────┘                    └──────────────────┘                  └──────────────┘
        │                                      │
        └────────── MetaMask doğrudan imza ────┘
```

- **Yazma işlemleri** (iş yayınlama, başvuru, onay, punch-in/çıkış, ödeme): önyüz, MetaMask ile imzaladıktan sonra zincir üzerinde işlem başlatır; işlem başarılı olduktan sonra MySQL'e yazmak üzere backend'e geri bildirim gönderir
- **Okuma işlemleri** (iş listesi, kontrol paneli, denetim kaydı): backend MySQL'den okur, böylece görüntüleme hızı garanti edilir
- **Otomatik ödeme**: backend iş parçacığı periyodik olarak `executeAutoSettlement()` / `autoConfirmStaleClockIns()` çağırır

---

## Dizin Yapısı

```
Campus/
├── contracts/
│   └── WorkStudy.sol        # Çekirdek akıllı kontrat
├── frontend/
│   └── index.html           # Frontend tek sayfa uygulaması
├── scripts/
│   └── deploy.js            # Kontrat deploy betiği (dinamik gas + deploy doğrulaması)
├── main.py                  # FastAPI backend (veritabanı kurulumu ve tüm API'ler dahil)
├── start.bat                # Windows tek tıkla başlatma
├── hardhat.config.js        # Hardhat yapılandırması (Geth localhost'a bağlanır)
├── uploads/                 # Kullanıcıların yüklediği avatarlar (çalışma anında oluşur)
├── contract_address.txt     # Mevcut deploy edilmiş kontrat adresi (deploy.js tarafından oluşturulur)
└── venv/                    # Python sanal ortamı
```

---

## Çevre Bağımlılıkları

| Bağımlılık | Sürüm Gereksinimi | Açıklama |
|---|---|---|
| Geth | 1.10+ | Yerel geliştirme zinciri, Chain ID 887766 |
| Node.js | 18+ | Hardhat / deploy.js çalıştırmak için |
| Python | 3.10+ | Backend |
| MySQL | 5.7+ / 8.0 | Veritabanı adı `campus_db`, root şifresi `123456` |
| MetaMask | Tarayıcı uzantısı | Önyüzde işlem imzalama |

Python bağımlılıkları (`venv/` içinde kurulu): `fastapi` `uvicorn` `web3` `sqlalchemy` `pymysql`

---

## Hızlı Başlangıç

### 1. Geth geliştirme zincirini başlatma

```bash
geth --dev --http --http.addr 0.0.0.0 --http.port 8545 \
     --http.api eth,net,web3,personal,miner \
     --http.corsdomain "*" --datadir geth_data
```

Başlatıldıktan sonra MetaMask'e ağı ekleyin:
- RPC URL: `http://127.0.0.1:8545`
- Chain ID: `887766`
- Para birimi simgesi: `ETH`

### 2. Veritabanını ilklendirme

MySQL'de `campus_db` veritabanını oluşturun. Backend ilk kez başlatıldığında tabloları otomatik oluşturur:

```sql
CREATE DATABASE campus_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### 3. Tek tıkla başlatma

```bat
start.bat
```

Betik sırasıyla şunları gerçekleştirir: iş tablolarını temizleme (isteğe bağlı) → kontratı deploy et → backend'i başlat → tarayıcıyı aç.

### 4. Elle başlatma (isteğe bağlı)

```bash
# Kontratı deploy et
npx hardhat run scripts/deploy.js --network localhost

# Backend'i başlat
venv\Scripts\activate
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Başlatıldıktan sonra şu adreslere erişin:
- Frontend: http://127.0.0.1:8000
- API dokümantasyonu: http://127.0.0.1:8000/docs

---

## Kullanıcı Rolleri

| Rol | Yetenekler |
|---|---|
| `STUDENT` | İş ilanlarını görüntüleme, başvuru yapma, mesaiye giriş/çıkış kaydı (punch-in/out), çalışma saati ve ödemeyi görüntüleme, istifa etme |
| `TEACHER` | İş yayınlama (bütçe kilitlenmeli), başvuruları onaylama, çalışma saatini onaylama, maaş ödeme, işi kapatma |
| `ADMIN` | Kullanıcı yönetimi, iş yönetimi, sistem istatistikleri, ödeme yapılandırması, denetim kaydı, zincir üzerindeki ID onarımı |

Öğretmen kimliği zincir üzerinde `registerAsTeacher()` beyaz listesiyle kaydedilir; backend rolleri MySQL `users.role` alanında saklanır.

---

## Akıllı Kontratın Çekirdek Süreci

```
Öğretmen kaydı ─▶ createJob (bütçeyi kilitle)
                    │
Öğrenci başvurusu ─▶ applyForJob ─▶ [PENDING] ─▶ Algoritmik eşleşme puanı (TOPSIS)
                    │
Öğretmen onayı ─▶ approveApplication ─▶ [APPROVED] ─▶ punch-in clockIn / clockOut
                    │                                        │
                    │              (çalışma saati = block.timestamp ile otomatik hesaplanır,
                    │               öğrenci süreyi elle giremez)
                    │              (IP kaydı + risk tespiti + çalışma saati kırmızı çizgisi kontrolü)
                    │                                        │
                    │                          confirmHours (çalışma saatini onayla)
                    │                                        │
                    └── rejectApplication ─▶ [REJECTED]    settleSalary (maaşı öde)
                                                          │
                                       closeJob / SBT madalyası ver (ERC-5192)
                                                          │
                                    Öğrenci ret oylamasına itiraz edebilir → yönetici hakemliği
```

**Hile karşıtı zaman ölçüm mekanizması**:
- `clockOut` artık elle girilen çalışma saatini kabul etmez; süre kontrat tarafından `block.timestamp - clockInTime` formülüyle otomatik hesaplanır (dakikaya doğru aşağı yuvarlanır); önyüz yalnızca canlı bir zamanlayıcı gösterir
- Tek punch-in üst sınırı 8 saattir (480 dakika): aşan kısım işlemi geri almak yerine kırpılır, çıkış yapmayı unutan öğrenci sisteme kilitlenmez
- `rejectClockIn` zincir üzerinde mezartaşı işaretidir (`workMinutes=0, confirmed=true`): öğretmenin reddettiği kayıtlar nemalandırma süresi (grace period) içinde otomatik onaylanmaz ve ücrete tabi tutulmaz

**Otomatik ödeme mekanizması**:
- `settlementConfig.cycleDays`: ödeme döngüsü (gün)
- `gracePeriodHours`: çalışma saati onayı için nemalandırma süresi; aşıldığında `autoConfirmStaleClockIns()` otomatik onay çalıştırır (`rejectClockIn` ile reddedilen kayıtlar mezartaşı işareti nedeniyle bu kapsama girmez)
- `isSettlementDue()` süresi dolduğunda `autoSettler` tarafından `executeAutoSettlement()` toplu ödemesi çağrılır

Temel olaylar (event): `JobCreated` / `ApplicationSubmitted` / `ApplicationApproved` / `ClockedIn` / `HoursConfirmed` / `SalarySettled` / `AutoSettlementExecuted`

---

## Temel API'ler

Tam dokümantasyon: http://127.0.0.1:8000/docs

| Kategori | Uç Noktalar |
|---|---|
| Kimlik doğrulama | `POST /api/login` `POST /api/register` `POST /api/login_by_wallet` `POST /api/bind_wallet` |
| İş ilanları | `POST /api/save_job` `GET /api/public_jobs` `GET /api/job/{id}` `POST /api/delete_job` |
| Başvurular | `POST /api/apply_job` `POST /api/approve_application` `POST /api/reject_application` `POST /api/resign` |
| Punch-in/out | `POST /api/clock_in` `POST /api/clock_out` `POST /api/confirm_hours` `POST /api/reject_clock_in` |

> **Çalışma saati açıklaması**: `clock_in` yanıtı `clock_in_time` içerir (önyüzün zamanlayıcı başlangıç noktası); `clock_out` isteği yalnızca `clock_in_id` gerektirir, çalışma saati zincir/sunucu üzerinde zaman damgasına göre otomatik hesaplanır ve yanıt `work_minutes` değerini döndürür.

| Ödeme | `POST /api/settle_salary` `GET /api/settlements/{wallet}` `GET /api/pending_settlements/{employer}` |
| Kontrol paneli | `GET /api/my_dashboard/{wallet}` `GET /api/teacher_dashboard/{wallet}` |
| Yönetim | `GET /api/admin/stats` `GET /api/admin/users` `GET /api/admin/jobs` `POST /api/admin/delete_all_jobs` |
| Otomatik ödeme | `GET /api/settlement_config` `POST /api/settlement_config` `POST /api/trigger_auto_settlement` |
| Diğer | `GET /api/audit_logs` `POST /api/repair_chain_ids` `POST /api/upload_avatar` |
| **Hedefli destek** | `POST /api/student_profile` `GET /api/student_profile/{wallet}` `GET /api/match_score/{eid}` `GET /api/admin/verify_profiles` `POST /api/admin/verify_profile` |
| **Uyum risk kontrolü** | `GET /api/student_hour_status/{wallet}` `GET /api/risk_alerts` |
| **SBT madalyası** | `GET /api/badges/{wallet}` `GET /api/verify_badge/{token_id}` |
| **Anlaşmazlık hakemliği** | `POST /api/disputes` `GET /api/disputes/{wallet}` `GET /api/admin/disputes` `POST /api/admin/resolve_dispute` |

---

## İleri Düzey Özellikler

### 1. Hedefli Destek: İnsan-İş Akıllı Eşleştirme Algoritması (TOPSIS)

Eğitim Bakanlığı, iş-öğrenim pozisyonlarının ailece ekonomik zorluk çeken öğrencilere öncelik verilmesini şart koşar. Sistem, insan-iş eşleştirmesi için çok kriterli ağırlıklı TOPSIS algoritmasını uygular:

**Puan = w₁×Zorluk düzeyi + w₂×Boş zaman uyumluluğu + w₃×Geçmiş güven puanı − w₄×Bu ay alınan burs**

| Gösterge | Ağırlık | Açıklama |
|---|---|---|
| Zorluk düzeyi | 0.35 | A=1,0, B=0,7, C=0,4, normal=0,1 |
| Boş zaman uyumluluğu | 0.25 | min(kullanılabilir saat / işin gerektirdiği saat, 1,0) |
| Geçmiş söz tutma güven puanı | 0.25 | 0-100 arası normalize edilmiş |
| Bu ay alınan burs | 0.15 | Negatif gösterge, düşük olması iyidir |

Öğretmen onayında algoritmik eşleşme yüzdesi gösterilir; ekonomik zorluk çeken öğrenciler otomatik olarak en üste taşınır.

### 2. SBT Söz Tutma Kanıtı (ERC-5192 Soulbound Token)

ERC-5192 standardı (Kilitli Soulbound Token) üzerine kurulu, devredilemez zincir içi söz-tutma madalyası:

- `contracts/WorkStudySBT.sol`: sadeleştirilmiş ERC-721 uygulaması + soulbound kilidi
- İş kapanışında öğretmen madalya verir; pozisyon adı, bölüm, birikmiş çalışma saati, yorum ve yıldız derecesi kaydedilir
- Herkesin sorgulayabileceği açık doğrulama arayüzü; herkes madalyanın gerçek olup olmadığını doğrulayabilir
- Token öğrencinin cüzdanına bağlanır; transfer/işlem yasağı

### 3. Uyum Riski Kontrolü: Çalışma Saati Kırmızı Çizgisi ve Vekil Çalışma Önleme

Eğitim Bakanlığı politika uyumluluğu: "haftada 8 saati, ayda 40 saati aşmayı"

- **Çalışma saati kırmızı çizgisi**: punch-in sırasında haftalık/aylık kota kontrol edilir, aşımda otomatik olarak engellenir
- **IP anomali tespiti**: punch-in kaydı istemci IP'sini tutar; giriş-çıkış IP'leri farklı ağ segmentinden geliyorsa `IP_MISMATCH` olarak işaretlenir
- **İlerleme çubuğu görselleştirmesi**: öğrenci ekranında haftalık/aylık çalışma saati kotasının kullanımı gösterilir

### 4. Haklarda Tam Kapanış: Anlaşmazlık İtirazı ve Hakemlik

Öğretmen punch-in kaydını reddettikten sonra öğrenci 72 saat içinde itiraz başlatabilir:

1. Öğrenci itiraz gerekçesini doldurur ve kanıt materyalinin hash'ini gönderir
2. Durum `IN_DISPUTE` haline gelir
3. Yönetici, hakemlik panelinde iki tarafın kayıtlarını inceleyip tek tıkla karar verir:
   - **Reti onama**: kayıt mezartaşı durumunda kalır
   - **Zorla onama**: çalışma saati geri yüklenip toplanır ve maaşa yansıtılır

---

## Deploy Betiği Açıklaması

`scripts/deploy.js`, Geth ile sabit kodlu (hardcoded) JSON-RPC üzerinden haberleşir (Hardhat'ın yerleşik ağında değil) ve deploy başarısız olsa bile ölü adresin yazılmasını önlemek için üçlü doğrulama yapar:

1. `eth_estimateGas` × 1.3 ile dinamik gas hesabı (WorkStudy deployu yaklaşık 525 milyon gas gerektirir)
2. `receipt.status === 0x1` doğrulaması
3. `eth_getCode` ile adreste gerçekten kod bulunduğunu teyit

Deploy başarılı olduktan sonra `frontend/index.html` içindeki `CONTRACT_ADDR` değerini ve kök dizindeki `contract_address.txt` dosyasını otomatik günceller.

> **Ders**: Erken sürümlerde sabit `gas: 4 milyon` kullanılması deploy sırasında out-of-gas hatasına yol açtı; ancak Geth, başarısız deploy için de `contractAddress` döndürdüğü için betik status'ü kontrol etmeden ölü adresi önyüze yazdı ve sonrasında tüm "kontrat çağrıları" fiilen boş adrese transfer olup ETH'nin kaybolmasına neden oldu. **Yeni deploy betikleri status + getCode doğrulaması yapmak zorundadır.**

---

## Sıkça Sorulan Sorular

**Kontrat çağrısı "başarılı" ama log yok, view dönüşü `0x`?**
Önce `eth_getCode` çalıştırarak kontrat adresinde kod olduğunu teyit edin; büyük olasılıkla deploy başarısız olup ölü adres yazılmıştır.

**MetaMask işlemi takılıyor/başarısız oluyor mu?**
Geth dev modunda hesapların kilidi açılmalıdır. Deploy hesabı dev önceden açılmış hesaptır; MetaMask hesabından işlem göndermek için `personal.unlockAccount` gerekir ya da farklı bir imza yöntemine geçilmelidir.

**Tarayıcı konsolunda `content_main.js toLowerCase` hatası mı alıyorsunuz?**
Buna Edge uzantısı neden olur ve bu proje ile ilgili değildir.

**Öğrenciler çalışma saatini sahte olarak bildirebilir mi?**
Hayır. Çalışma saati kontrat tarafından `block.timestamp` kullanılarak otomatik hesaplanır; önyüz yalnızca canlı zamanlayıcıyı gösterir ve doldurulacak bir alan yoktur. Punch-in'den 60 saniye sonra çıkış yapılması 0 dakika yazılır; çıkış yapılmayıp 8 saati aşma durumunda süre 480 dakikaya kırpılır (öğretmen, bekleyen onay listesindeki anormal kayıtları reddedebilir; reddedilen kayıtlar zincir üzerindeki mezartaşı sayesinde otomatik ödemeye dahil edilmez).

**`start.bat` verileri siler mi?**
Varsayılan olarak silmez. Betik başlatılırken iş tablolarını (jobs / employments / clock_ins / settlements) temizleyip temizlemeyeceğinizi sorar; **varsayılan olarak veriler korunur** (doğrudan Enter'a basın); `Y` girilirse temizlenir. Kullanıcı tablosu her zaman korunur.
